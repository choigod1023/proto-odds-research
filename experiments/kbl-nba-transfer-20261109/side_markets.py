"""Score KBL full-game half-lines from hash-verified Betman round details."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import json
import math
from pathlib import Path
import sys

# Keep this runner's writes confined to the explicit output directory.
sys.dont_write_bytecode = True

import numpy as np
from scipy.special import ndtr

import run

sys.path.insert(0, str(run.ROOT / "src"))
from league_handicap import ARTIFACT_SHA256, recommend


KST = timezone(timedelta(hours=9))
CUTOFF = "2025-12-27"
MODELS = ("score_form", "score_components", "score_rotation")
MARKETS = {
    (2, None, None): "handicap",
    (23, "6", "5"): "handicap",
    (9, None, None): "total",
    (9, "80", "7"): "total",
}
SIDES = {"handicap": ("home", "away"), "total": ("under", "over")}
FROZEN = "kbl-team-margin-handicap-v1"
RUN_PATH = Path(run.__file__)


def source_reference(path):
    path = path.resolve()
    if path.is_relative_to(run.ROOT):
        return path.relative_to(run.ROOT).as_posix()
    return path.name


def official_index(games):
    by_id = {game["id"]: game for game in games}
    official = defaultdict(dict)
    for raw in run.FIXTURES.read_bytes().splitlines():
        record = json.loads(raw)
        if record["league"] == "KBL" and record["kind"] in ("fixture", "result"):
            ident = record["payload"]["source_fixture_id"]
            if ident in by_id:
                official[ident][record["kind"]] = record

    index = defaultdict(list)
    for ident, records in official.items():
        fixture = records["fixture"]
        kickoff = datetime.fromisoformat(fixture["kickoff"])
        game = by_id[ident]
        index[(int(kickoff.timestamp() * 1000), *game["teams"])].append(
            (game, kickoff.isoformat(), records["result"])
        )
    return index


def grade(row, market, line, score):
    home, away = score
    if market == "handicap":
        adjusted = (Decimal(home) + Decimal(str(line)), Decimal(away))
        first_wins = adjusted[0] > adjusted[1]
    else:
        adjusted = (Decimal(home + away), Decimal(0))
        first_wins = adjusted[0] < Decimal(str(line))
    try:
        official_score = tuple(Decimal(part) for part in (row["mchScore"] or "").split(":"))
    except InvalidOperation:
        return None
    code = "0" if first_wins else "2"
    if adjusted != official_score or row["gameResult"] != code:
        return None
    return int(first_wins)


def price_terms(row, market):
    line = row["winHandi"]
    odds = [row["winAllot"], row["loseAllot"]]
    if (
        type(line) not in (int, float)
        or line % 1 != 0.5
        or (market == "total" and line <= 0)
        or row["loseHandi"] != (-line if market == "handicap" else line)
        or row["drawAllot"] != 0
        or row["gameSubject"] not in (None, "")
        or not all(type(p) in (int, float) and math.isfinite(p) and p > 1 for p in odds)
        or row["gameReject"] != "0"
        or row["protoStatus"] not in ("4", "20")
        or row["gameResult"] not in ("0", "2")
    ):
        return None
    return line, odds


def collect(manifest_path, index):
    manifest = json.loads(manifest_path.read_bytes())
    sources = manifest["sources"]
    rounds = [source["round"] for source in sources]
    if len(set(rounds)) != len(rounds) or len(rounds) != manifest["source_round_count"]:
        raise ValueError("duplicate or missing manifest round")
    seen, rows = set(), []
    audit = defaultdict(Counter)
    for source in sources:
        root = Path(manifest["source_roots"][source["source_root_index"]])
        path = root / source["body_file"]
        body = gzip.decompress(path.read_bytes())
        if run.sha(body) != source["body_sha256"]:
            raise ValueError(f"Betman response hash mismatch: {path}")
        detail = json.loads(body)
        if str(detail["gmTs"]) != source["round"]:
            raise ValueError(f"Betman round mismatch: {path}")
        schedules = detail["compSchedules"]
        for values in schedules["datas"]:
            row = dict(zip(schedules["keys"], values, strict=True))
            if row["leagueCode"] != "BK001":
                continue
            market = MARKETS.get((row["handi"], row["betId"], row["betTypId"]))
            if market is None:
                continue
            ts = row["gameDate"]
            day = datetime.fromtimestamp(ts / 1000, KST).date().isoformat()
            counts = audit[f"{day[:4]}_{market}"]
            counts["raw_offers"] += 1
            match_seq = row["matchSeq"]
            if type(match_seq) is not int or match_seq <= 0:
                raise ValueError(f"invalid Betman matchSeq in {path}: {match_seq!r}")
            offer_id = f"G101:{source['round']}:{match_seq}"
            if offer_id in seen:
                raise ValueError(f"duplicate Betman product: {offer_id}")
            seen.add(offer_id)

            terms = price_terms(row, market)
            if terms is None:
                counts["invalid_or_unpriced"] += 1
                continue
            line, odds = terms
            games = index[(ts, str(row["homeId"]), str(row["awayId"]))]
            if len(games) != 1:
                counts["no_unique_official_result"] += 1
                continue
            game, kickoff, official = games[0]
            y = grade(row, market, line, game["score"])
            if y is None:
                counts["settlement_conflict"] += 1
                continue
            counts["matched"] += 1
            counts["buy_reject_zero"] += row["buyReject"] == "0"
            rows.append({
                "id": game["id"], "day": day, "kickoff": kickoff, "market": market,
                "offer_id": offer_id, "round": source["round"], "match_seq": match_seq,
                "team_ids": list(game["teams"]), "team_names": [row["homeName"], row["awayName"]],
                "line": line, "odds": odds, "sides": SIDES[market], "y": y,
                "score": game["score"], "overtime_periods": game.get("overtime"),
                "official_grade": SIDES[market][1 - y], "betman_result_code": row["gameResult"],
                "betman_result_score": row["mchScore"], "buy_reject": row["buyReject"],
                "game_reject": row["gameReject"], "proto_status": row["protoStatus"],
                "market_codes": {"handi": row["handi"], "bet_id": row["betId"], "bet_type_id": row["betTypId"]},
                "period": "full_including_extra", "settlement_rule": f"{market}_half",
                "betman_source": {
                    "file": source_reference(path), "manifest_root_index": source["source_root_index"],
                    "sha256": source["body_sha256"], "url": source["source_url"],
                    "archive_received_at": source["received_at"], "timestamp_basis": source["timestamp_basis"],
                },
                "kbl_result_source": {
                    "record_id": official["record_id"], "sha256": official["body_sha256"],
                    "url": official["source_url"],
                },
            })
    rows.sort(key=lambda row: (row["day"], row["kickoff"], row["offer_id"]))
    return rows, audit, len(rounds)


def score_axis(pair, market):
    home, away = pair
    return float(home - away if market == "handicap" else home + away)


def fit(rows, forecasts, market):
    training = {row["id"]: row for row in rows if "2025-01-01" <= row["day"] <= CUTOFF}
    if len(training) < 2:
        raise ValueError(f"insufficient 2025 calibration fixtures for {market}")
    fits = {}
    for model in MODELS:
        error = np.asarray([
            score_axis(row["score"], market) - score_axis(forecasts[ident][model], market)
            for ident, row in training.items()
        ])
        bias, sd = float(error.mean()), float(error.std(ddof=0))
        if not math.isfinite(bias) or not math.isfinite(sd) or sd <= 0:
            raise ValueError(f"invalid 2025 error distribution for {market}/{model}")
        fits[model] = {"bias": bias, "sd": sd}
    return {
        "n": len(training), "fixture_ids": list(training),
        "date_range": [min(r["day"] for r in training.values()), max(r["day"] for r in training.values())],
        "fits": fits,
    }


def frozen_probability(row):
    # The date gates the frozen artifact's training cutoff. It is not a quote observation.
    kickoff = datetime.fromisoformat(row["kickoff"])
    sides = ("핸디홈", "핸디원정")
    offers = {
        side: {"배당": price, "offer_id": row["offer_id"], "selection_id": side}
        for side, price in zip(sides, row["odds"], strict=True)
    }
    terms = {
        "sport": "bk", "league": "KBL", "market": "handicap", "line": row["line"],
        "period": row["period"], "settlement_rule": row["settlement_rule"],
    }
    game = dict(zip(("home", "away"), row["team_names"], strict=True))
    arm = recommend(terms, game, offers, price_time=kickoff)
    if arm is None:
        return None
    return arm["probability"] if arm["selection"] == sides[0] else 1 - arm["probability"]


def predict(row, forecast, fits):
    market = row["market"]
    first, second = row["odds"]
    prob = {"market": second / (first + second)}
    for model, params in fits.items():
        center = score_axis(forecast[model], market) + params["bias"]
        edge = center + row["line"] if market == "handicap" else row["line"] - center
        prob[model] = float(ndtr(edge / params["sd"]))
    if market == "handicap" and (frozen := frozen_probability(row)) is not None:
        prob[FROZEN] = frozen
    picks = {}
    for name, p in prob.items():
        selected = int(p >= 0.5)
        hit = selected == row["y"]
        price = row["odds"][1 - selected]
        picks[name] = {
            "side": row["sides"][1 - selected], "price": price, "hit": hit,
            "net_units": (price if hit else 0) - 1,
        }
    return dict(row, prob=prob, picks=picks, history=forecast["history"],
                score_forecasts={name: forecast[name] for name in MODELS})


def summarize(rows, names):
    if not rows:
        return {"n": 0, "status": "no_eligible_offers"}
    metrics = run.metrics(rows, names)
    for model in names:
        metrics[model]["brier"] = float(np.mean([(row["prob"][model] - row["y"]) ** 2 for row in rows]))
        metrics[model]["probability_ties"] = sum(row["prob"][model] == 0.5 for row in rows)
    references = ["score_form"]
    if FROZEN in names:
        references.append(FROZEN)
    for reference in references:
        comparison = run.metrics(rows, ("market", reference, "score_rotation"))
        key = "vs_" + reference
        metrics["score_rotation"][key] = comparison["score_rotation"][key]
    return {
        "n": len(rows), "fixtures": len({row["id"] for row in rows}),
        "date_range": [rows[0]["day"], rows[-1]["day"]],
        "equal_price_offers": sum(row["odds"][0] == row["odds"][1] for row in rows),
        "overtime_offers": sum((row["overtime_periods"] or 0) > 0 for row in rows),
        "overtime_unknown_offers": sum(row["overtime_periods"] is None for row in rows),
        "metrics": metrics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boxes", type=Path, required=True, help="KBL player-stat receipt and gzip directory")
    parser.add_argument("--evidence", type=Path, required=True, help="Betman manifest-v2.json or its directory")
    parser.add_argument("--out", type=Path, required=True, help="directory for results and predictions")
    args = parser.parse_args()
    manifest = args.evidence / "manifest-v2.json" if args.evidence.is_dir() else args.evidence
    paths = {
        "side_markets": Path(__file__), "run": RUN_PATH, "kbl_official": run.FIXTURES,
        "betman_manifest": manifest, "frozen_model": run.ALIASES,
        "frozen_recommend": Path(sys.modules[recommend.__module__].__file__),
    }
    hashes = {name: run.sha(path.read_bytes()) for name, path in paths.items()}
    games, source_audit, receipts = run.load(args.boxes)
    schedule, rosters = run.profiles_index(games)
    rows, audit, rounds = collect(manifest, official_index(games))
    needed = {row["id"] for row in rows}
    forecasts = {
        game["id"]: run.features(game, schedule, rosters) for game in games if game["id"] in needed
    }

    predictions, results, calibration = [], {}, {}
    names = ("market", *MODELS)
    for market in SIDES:
        eligible = []
        for row in rows:
            if row["market"] != market:
                continue
            available = forecasts[row["id"]] is not None
            audit[f"{row['day'][:4]}_{market}"]["feature_eligible" if available else "incomplete_history"] += 1
            if available:
                eligible.append(row)
        calibration[market] = fit(eligible, forecasts, market)
        test = [
            predict(row, forecasts[row["id"]], calibration[market]["fits"])
            for row in eligible if row["day"].startswith("2026-")
        ]
        predictions.extend(test)
        buy_zero = [row for row in test if row["buy_reject"] == "0"]
        for cohort, subset in (("all_valid_priced", test), ("buy_reject_zero", buy_zero)):
            non_ties = [row for row in subset if row["odds"][0] != row["odds"][1]]
            for suffix, paired in (("", subset), ("_no_market_price_ties", non_ties)):
                key = f"{market}_{cohort}{suffix}"
                results[key] = summarize(paired, names)
                if market == "handicap":
                    frozen_subset = [row for row in paired if FROZEN in row["prob"]]
                    results[key + "_frozen_paired"] = summarize(frozen_subset, (*names, FROZEN))

    if any(run.sha(path.read_bytes()) != hashes[name] for name, path in paths.items()):
        raise RuntimeError("an input changed during the run; rerun on stable inputs")
    predictions.sort(key=lambda row: (row["day"], row["kickoff"], row["offer_id"]))
    body = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
                    for r in predictions)
    compressed = gzip.compress(body, mtime=0)
    summary = {
        "protocol": {
            "markets": "Betman KBL full-game 2-way half-lines, overtime included",
            "calibration": "unique 2025 Betman-matched games per market; final score minus model forecast",
            "train_through": CUTOFF, "test_year": 2026,
            "history": "run.features D-4/last 10 games, transfer-aware rosters, box only",
            "ties": "first side (home or under), same rule for all models and market",
            "market_tie_sensitivity": "exclude equal-price offers from every arm on the same paired cohort",
            "price_scope": "postgame archived prices; no purchase-time or execution evidence",
            "sensitivity": "buyReject=0 on evaluation only; same calibration as all priced offers",
            "probability_axis": {"handicap": "home", "total": "under"},
            "roi": "hypothetical one unit per offer",
        },
        "inputs": {name: {"path": source_reference(path), "sha256": hashes[name]} for name, path in paths.items()},
        "frozen_artifact_sha256_canonical": ARTIFACT_SHA256,
        "rounds_hash_verified": rounds, "source_audit": source_audit, "matching": audit,
        "calibration": calibration, "runs": results,
        "predictions_count": len(predictions), "predictions_sha256_uncompressed": run.sha(body),
        "predictions_sha256_gzip": run.sha(compressed),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("results.json", summary), ("box-receipts.json", receipts)):
        (args.out / filename).write_bytes(
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        )
    (args.out / "predictions.jsonl.gz").write_bytes(compressed)
    for name, result in results.items():
        print(f"{name}: {result['n']}")
        for model, m in result.get("metrics", {}).items():
            print(f"  {model:28} {m['hits']:3} {m['accuracy']:7.2%} {m['roi']:8.2%} {m['logloss']:.5f}")


if __name__ == "__main__":
    main()
