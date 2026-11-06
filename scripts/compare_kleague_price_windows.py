"""Export exact DB quotes and score verified K League price pairs offline."""
import argparse
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kleague_price_windows import (FIRST_ROUND, KST, catalog, eligible_windows, market,
                                  read_body, read_json, sha)
from snapshot import _snapshot_record
from wisetoto import _decode, parse_rows

# Fixed on the 62 historical training matches, before the prospective sample.
MOVEMENT_WEIGHT = -0.528823663214722
MIN_MATCHES = 30
MIN_ROUNDS = 3
SCHEDULE_URL = "https://www.kleague.com/getScheduleList.do"


def rounds(root):
    for path in sorted(root.glob("*/windows.json")):
        if re.fullmatch(r"\d{4}-\d{1,4}", path.parent.name):
            year, number = map(int, path.parent.name.split("-"))
            if (year, number) >= FIRST_ROUND:
                yield path.parent, year, number, read_json(path)


def quote_key(record):
    return record["year"], record["round"], str(record["game_no"]), record["ts"]


def db_quotes(root, database):
    """Read only the indexed rows named by the archived receipts."""
    out = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as conn:
        conn.execute("PRAGMA query_only=ON")
        for _, _, _, manifest in rounds(root):
            for phases in manifest.values():
                for observation in phases.values():
                    key = quote_key(observation["db_quote"])
                    row = conn.execute("""
                        SELECT season AS year, round, game_no, observed_at AS ts,
                               sport, league, market_family, n_way, market_label,
                               home, away, date_text, odds_json, result
                        FROM odds_snapshots WHERE season=? AND round=? AND game_no=? AND observed_at=?
                        """, key).fetchone()
                    if row:
                        out[key] = dict(zip(("year", "round", "game_no", "ts", "sport", "league", "market_family",
                                            "n_way", "market_label", "home", "away", "date_text", "odds_json",
                                            "result"), row, strict=True))
    return list(out.values())


def same_quote(db_row, record):
    return (db_row is not None and json.loads(db_row["odds_json"]) == list(map(float, record["odds"].split(",")))
            and all(db_row.get(key) == value for key, value in record.items() if key != "odds"))


def verified_pairs(root, db_rows):
    quotes = {quote_key(row): row for row in db_rows}
    counts = dict(opening_observations=0, t30_observations=0, complete_windows=0,
                  verified_db_pairs=0, evidence_mismatch=0)
    for directory, year, number, manifest in rounds(root):
        proof = read_json(directory / "catalog.json")
        reference = catalog(read_body(directory, proof["body_sha256"]), year, number)
        observed = datetime.fromisoformat(proof["received_at"])
        if (reference is None or any(proof.get(key) != value for key, value in reference.items())
                or not datetime.fromisoformat(reference["opened"]) <= observed < datetime.fromisoformat(reference["closing"])):
            raise ValueError(f"catalog proof differs: {directory.name}")
        bodies = {}
        for product, phases in manifest.items():
            counts["opening_observations"] += "opening" in phases
            counts["t30_observations"] += "t30" in phases
            if not {"opening", "t30"} <= phases.keys():
                continue
            counts["complete_windows"] += 1
            valid = []
            for phase in ("opening", "t30"):
                observation = phases[phase]
                received = datetime.fromisoformat(observation["received_at"])
                digest = observation["body_sha256"]
                if digest not in bodies:
                    raw = read_body(directory, digest)
                    html = _decode(SimpleNamespace(content=raw, text=raw.decode("utf-8", errors="replace")))
                    bodies[digest] = parse_rows(html, year, number)
                candidates = [row for row in bodies[digest] if str(row.game_no) == product]
                if len(candidates) != 1:
                    break
                row = candidates[0]
                record = _snapshot_record(row, received.isoformat(timespec="seconds"))
                if (not market(row) or phase not in eligible_windows(row, reference, received)
                        or record != observation["db_quote"] or not same_quote(quotes.get(quote_key(record)), record)):
                    break
                valid.append((received, row.odds))
            if len(valid) != 2 or valid[0][0] >= valid[1][0]:
                counts["evidence_mismatch"] += 1
                continue
            counts["verified_db_pairs"] += 1
            yield counts, dict(round=f"{year}-{number}", product=product, **reference["products"][product],
                               opening=valid[0][1], odds=valid[1][1], as_of=valid[1][0].isoformat())
    yield counts, None


def official_schedule(league, year, month):
    request = dict(leagueId=league, year=str(year), month=f"{month:02d}", teamId="", ticketYn="")
    with requests.post(SCHEDULE_URL, json=request, timeout=(5, 10), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"K League schedule HTTP {response.status_code}")
        raw = response.raw.read(3_000_001, decode_content=True)
    if len(raw) > 3_000_000:
        raise ValueError("K League schedule exceeds size limit")
    doc = json.loads(raw)
    if str(doc["resultCode"]) != "200":
        raise ValueError("K League schedule did not succeed")
    return sha(raw), doc["data"]["scheduleList"]


def implied(odds):
    inverse = [1 / value for value in odds]
    return [value / sum(inverse) for value in inverse]


def metrics(rows, *, movement):
    losses, profit, hits = [], [], 0
    for row in rows:
        probability = implied(row["odds"])
        if movement:
            opening = implied(row["opening"])
            values = [p * (p / o) ** MOVEMENT_WEIGHT for p, o in zip(probability, opening, strict=True)]
            probability = [v / sum(values) for v in values]
        pick = max(range(3), key=probability.__getitem__)
        hit = pick == row["outcome"]
        hits += hit
        profit.append(row["odds"][pick] - 1 if hit else -1)
        losses.append(-math.log(probability[row["outcome"]]))
    n = len(rows)
    return dict(log_loss=math.fsum(losses)/n, hits=hits, hit_rate=hits/n, roi=math.fsum(profit)/n)


def evaluate(root, db_rows, get_schedule=official_schedule):
    cache = {}
    matches = {}
    unresolved = 0
    for counts, pair in verified_pairs(root, db_rows):
        if pair is None:
            break
        kickoff = datetime.fromisoformat(pair["kickoff"]).astimezone(KST)
        league = int(pair["league"][-1])
        key = league, kickoff.year, kickoff.month
        if key not in cache:
            cache[key] = get_schedule(*key)
        digest, schedule = cache[key]
        candidates = [row for row in schedule if (row["leagueId"], row["gameDate"], row["gameTime"],
                       row["homeTeam"], row["awayTeam"]) == (league, kickoff.strftime("%Y.%m.%d"),
                       kickoff.strftime("%H:%M"), pair["home_id"], pair["away_id"])]
        if len(candidates) != 1:
            unresolved += 1
            continue
        row = candidates[0]
        home, away = row["homeGoal"], row["awayGoal"]
        if ((row["gameStatus"], row["endYn"]) != ("FE", "Y") or
                any(type(v) is not int or v < 0 for v in (home, away)) or
                row.get("homeGoal6") is not None or row.get("awayGoal6") is not None):
            unresolved += 1
            continue
        fixture = kickoff.year, league, row["gameId"]
        outcome = 0 if home > away else 1 if home == away else 2
        previous = matches.get(fixture)
        if previous is None or (pair["as_of"], pair["round"], pair["product"]) < (
                previous["as_of"], previous["round"], previous["product"]):
            matches[fixture] = dict(pair, fixture=fixture, outcome=outcome, official_schedule_sha256=digest)
    rows = list(matches.values())
    rounds_count = len({row["round"] for row in rows})
    result = dict(counts, settled=len(rows), unresolved=unresolved, rounds=rounds_count,
                  movement_weight=MOVEMENT_WEIGHT, metrics=None, fixtures=rows)
    if len(rows) >= MIN_MATCHES and rounds_count >= MIN_ROUNDS:
        baseline = metrics(rows, movement=False)
        adjusted = metrics(rows, movement=True)
        result["metrics"] = dict(baseline=baseline, movement=adjusted,
                                 difference={key: adjusted[key]-baseline[key] for key in baseline})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export-db-quotes", "score"))
    parser.add_argument("--evidence", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--db", type=Path)
    inputs.add_argument("--db-quotes", type=Path)
    args = parser.parse_args()
    if args.action == "export-db-quotes":
        if args.db is None:
            parser.error("export-db-quotes requires --db")
        result = db_quotes(args.evidence, args.db)
    else:
        quotes = db_quotes(args.evidence, args.db) if args.db else read_json(args.db_quotes)
        result = evaluate(args.evidence, quotes)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
