"""Freeze and settle a same-price NBA comparison alongside pregame capture."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import gzip
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

import requests

from scripts import nba_forward_capture as capture
from scripts.collect_proto_comparison_prices import write_json
from src.devig import market_probabilities

ARMS = ("q", "confirmed_player_spatial", "market")


def read_rows(db, table):
    # All callers pass internal table names.
    return {key: json.loads(value) for key, value in db.execute(f"SELECT game_id, record FROM {table}")}


def player_key(name):
    normalized = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in normalized if c.isalnum())


def lineup_rotation(row, side, game, grouped, state):
    team = row["teams"][0 if side == "home" else 1]
    history = sorted(state["clean_team"][team], key=lambda g: (g["day"], g["game_id"]))[-10:]
    if len(history) < 5:
        return None
    by_name = defaultdict(set)
    weights = Counter()
    for old_game in history:
        for person in grouped[old_game["game_id"], team]:
            seconds = person["minutes_seconds_displayed"] or 0
            if seconds > 0 and person["accounting_ok"]:
                by_name[player_key(person["name"])].add(person["athlete_id"])
                weights[person["athlete_id"]] += seconds
    starter_ids = {p["personId"] for p in row["starters"][side]}
    starter_names = {p["personId"]: player_key(p["playerName"]) for p in row["starters"][side]}
    matched = {}
    for person in game[side + "Team"]["players"]:
        nba_id = person["personId"]
        if nba_id not in row["active"][side]:
            continue
        candidates = by_name[player_key(person["playerName"])]
        if len(candidates) == 1:
            matched[nba_id] = next(iter(candidates))
    fallback_history = {}
    missing_names = {starter_names[pid] for pid in starter_ids - matched.keys()}
    if missing_names:
        past = defaultdict(lambda: defaultdict(list))
        for old_game in state["clean_games"]:
            for old_team in old_game["teams"]:
                for person in grouped[old_game["game_id"], old_team]:
                    name = player_key(person["name"])
                    seconds = person["minutes_seconds_displayed"] or 0
                    if name in missing_names and seconds > 0 and person["accounting_ok"]:
                        past[name][person["athlete_id"]].append((old_game["day"], old_game["game_id"], seconds))
        for nba_id in starter_ids - matched.keys():
            name = starter_names[nba_id]
            if len(by_name[name]) > 1 or len(past[name]) != 1:
                continue
            player_id, observations = next(iter(past[name].items()))
            recent = sorted(observations)[-10:]
            matched[nba_id] = player_id
            weights[player_id] = len(history) * sum(v[2] for v in recent) / len(recent)
            fallback_history[nba_id] = [v[1] for v in recent]
    if not starter_ids <= matched.keys() or len(set(matched.values())) != len(matched):
        return None
    selected = {pid: weights[pid] for pid in matched.values()}
    total = sum(selected.values())
    if not total:
        return None
    return {"minutes": {pid: 240 * seconds / total for pid, seconds in selected.items()},
            "history_game_ids": [g["game_id"] for g in history], "nba_to_espn": matched,
            "cross_team_starter_history": fallback_history}


def pinned_model(config_path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for path, expected in config["sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError("pinned NBA model or input changed")
    sys.path.insert(0, config["module_dir"])
    model = importlib.import_module("nba_player_spatial_model")
    pool, players, audits = (model.read_gzip(Path(config[key])) for key in ("pool", "players", "audits"))
    if any(capture.instant(p["source_received_at"]) >= capture.utc_now() for p in players):
        raise ValueError("future history receipt")
    clean, grouped, _ = model.reconcile_sources(pool, players, audits)
    return model, pool, clean, grouped, config


def freeze(db, config_path):
    captures = read_rows(db, "captures")
    forecasts = read_rows(db, "forecasts")
    pending = [row for key, row in captures.items() if key not in forecasts
               and capture.utc_now() < capture.instant(row["deadline"])]
    if not pending:
        return 0, {}
    model, pool, clean, grouped, config = pinned_model(config_path)
    states = {}
    held = Counter()
    saved = 0
    for row in pending:
        day = capture.instant(row["kickoff"]).astimezone(capture.KST).date().isoformat()
        if day not in states:
            states[day] = model.build_day_state(day, pool, clean, grouped)
        state = states[day]
        digest = row["receipts"]["lineup"]["sha256"]
        body = gzip.decompress(db.execute("SELECT gzip FROM bodies WHERE sha256 = ?", (digest,)).fetchone()[0])
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("archived lineup hash mismatch")
        game = next(g for g in json.loads(body)["games"] if g["gameId"] == row["game_id"])
        rotations = [lineup_rotation(row, side, game, grouped, state) for side in ("home", "away")]
        if any(r is None for r in rotations):
            held["incomplete_starter_history"] += 1
            continue
        calculated, reason = model.calculate_match(row, state, rotations)
        if reason:
            held[reason] += 1
            continue
        probabilities = dict(q=float(calculated["q_recomputed"]),
                             confirmed_player_spatial=float(calculated["player_spatial"]),
                             market=market_probabilities(row["odds"])[0])
        at = capture.utc_now()
        if at >= capture.instant(row["deadline"]) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
            held["deadline_or_invalid_probability"] += 1
            continue
        record = dict(frozen_at=at.isoformat(), odds=row["odds"], probabilities=probabilities,
                      rotations=rotations, model_commit=config["commit"],
                      inputs_sha256=config["sha256"], player_source_game_ids=calculated["player_source_game_ids"],
                      policy="confirmed-active-last-ten-team-240-v1")
        with db:
            saved += db.execute("INSERT OR IGNORE INTO forecasts VALUES (?, ?)",
                                (row["game_id"], json.dumps(record))).rowcount
    return saved, dict(held)


def official_result(row, snapshot):
    if capture.instant(snapshot[0]["received_at"]) <= capture.instant(row["kickoff"]):
        return None
    _, rows = capture.betman_round(snapshot, row["round"])
    result = rows.get(row["number"], {})
    if not capture.product(result) or list(capture.fields(result, capture.PRICE_FIELDS)) != row["identity"]:
        return None
    if result.get("protoStatus") != "4":
        return None
    code = result.get("gameResult")
    if (code == "4" and capture.fields(result, "winTxt drawTxt loseTxt") == (None, None, None)
            and capture.fields(result, "winAllot drawAllot loseAllot") == (0, 0, 0)):
        return {"status": "void", "receipt": snapshot[0]}
    score = re.fullmatch(r"([0-9]+):([0-9]+)", str(result.get("mchScore")))
    if not score or code not in ("0", "2") or capture.fields(result, "gameReject buyReject") != ("0", "0"):
        return None
    home, away = map(int, score.groups())
    if home == away or (home > away) != (code == "0"):
        return None
    return {"status": "settled", "winner": 0 if home > away else 1,
            "score": [home, away], "receipt": snapshot[0]}


def settle(db):
    now = capture.utc_now()
    resolved = read_rows(db, "settlements")
    rounds = defaultdict(list)
    for key, row in read_rows(db, "captures").items():
        if key not in resolved and now >= capture.instant(row["kickoff"]) + timedelta(hours=3):
            rounds[row["round"]].append(row)
    count = 0
    for round_id, group in sorted(rounds.items(), reverse=True)[:3]:
        key = "settlement:" + round_id
        checked = db.execute("SELECT meta FROM cache WHERE key = ?", (key,)).fetchone()
        if checked and now - capture.instant(checked[0]) < timedelta(minutes=30):
            continue
        with db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, now.isoformat(), b""))
        try:
            snapshot = capture.fetch_round(round_id)
            for row in group:
                result = official_result(row, snapshot)
                if result:
                    meta, raw = snapshot
                    with db:
                        db.execute("INSERT OR IGNORE INTO bodies VALUES (?, ?)",
                                   (meta["sha256"], gzip.compress(raw, compresslevel=3, mtime=0)))
                        count += db.execute("INSERT OR IGNORE INTO settlements VALUES (?, ?)",
                                            (row["game_id"], json.dumps(result))).rowcount
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"Settlement {round_id}: {type(exc).__name__}")
    return count


def comparison(db):
    captures = read_rows(db, "captures")
    forecasts = read_rows(db, "forecasts")
    settlements = read_rows(db, "settlements")
    paired = []
    void = 0
    for key in sorted(captures.keys() & forecasts.keys() & settlements.keys()):
        row, prediction, outcome = captures[key], forecasts[key], settlements[key]
        if not (capture.instant(row["paired_at"]) <= capture.instant(prediction["frozen_at"])
                < capture.instant(row["deadline"]) and prediction["odds"] == row["odds"]
                and set(prediction["probabilities"]) == set(ARMS)):
            continue
        if outcome["status"] == "void":
            void += 1
        elif outcome["status"] == "settled":
            paired.append((row, prediction, outcome))
    report = dict(captured=len(captures), forecasts=len(forecasts), official_results=len(settlements),
                  common_settled=len(paired), common_void=void, review_at=capture.MIN_SETTLED)
    if len(paired) >= capture.MIN_SETTLED:
        report["kickoff_range"] = [min(row["kickoff"] for row, _, _ in paired),
                                   max(row["kickoff"] for row, _, _ in paired)]
        report["models"] = {}
        for arm in ARMS:
            hits = 0
            gross = Decimal(0)
            for row, prediction, outcome in paired:
                side = 0 if prediction["probabilities"][arm] >= 0.5 else 1
                if side == outcome["winner"]:
                    hits += 1
                    gross += Decimal(str(row["odds"][side]))
            report["models"][arm] = dict(hits=hits, accuracy=hits / len(paired),
                                        roi=float((gross - len(paired)) / (len(paired) + void)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--report-file", type=Path)
    args = parser.parse_args()
    with closing(capture.database(args.db)) as db:
        if not args.report_only:
            try:
                print(json.dumps(capture.collect(db)))
            except (requests.RequestException, KeyError, ValueError) as exc:
                print(f"Capture: {type(exc).__name__}: {exc}")
            if args.model_config:
                print(json.dumps(dict(zip(("frozen", "held"), freeze(db, args.model_config)))))
            settle(db)
        report = comparison(db)
        if args.report_file:
            write_json(args.report_file, report)
        print(json.dumps(report))


if __name__ == "__main__":
    main()
