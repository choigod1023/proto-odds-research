"""Audit whether the NBA archives support a paired pregame workload comparison."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re

from kleague import jsonlines, materialize, module

NBA = "2df626042f633ba08f381f755ccc18f2068733f6"
OFFICIAL = "experiments/nba-official-information-20261103"
BASKETBALL = Path("experiments/pregame-basketball-20261104")
PRICES = Path("experiments/pregame-proto-prices-20261104/normalized-price-records-v2.jsonl.gz")


def fixture_key(row):
    payload = row["payload"]
    return datetime.fromisoformat(row["kickoff"]), payload["home_tricode"], payload["away_tricode"]


def injury_before_tip(row):
    return (row["payload"].get("information_type") == "injury_report"
            and row["source_url"].startswith("https://ak-static.cms.nba.com/referee/injury/")
            and row["timing_basis"] == "actual_receipt"
            and row["timestamp_basis"] == "http_response_body_completed"
            and datetime.fromisoformat(row["received_at"]) < datetime.fromisoformat(row["kickoff"]))


def audit(state, injury_pdf):
    source = materialize(state, NBA, OFFICIAL)
    reader = module(source / "audit.py")
    boxes = {}
    for path in sorted((source / "raw").glob("box-*.html")):
        game = reader.extract_postgame(path)
        raw = reader.read_props(path)["game"]
        team_minutes = {}
        for team in game["teams"]:
            players = {p["personId"]: p["statistics"]["minutes"] for p in raw[team["side"]]["players"]
                       if p.get("position")}
            if len(players) != 5 or any(not re.fullmatch(r"\d+:[0-5]\d", v) for v in players.values()):
                raise ValueError(f"invalid official starter minutes: {game['game_id']}")
            team_minutes[team["tricode"]] = players
        boxes[game["game_id"]] = dict(tip=game["tipoff_utc"], home=raw["homeTeam"]["teamTricode"],
                                      away=raw["awayTeam"]["teamTricode"], teams=team_minutes)

    fixtures = {}
    results = set()
    injuries = {}
    for filename in ("nba-2025-sample.jsonl", "nba-2026-sample.jsonl", "season-refresh-records.jsonl"):
        for row in jsonlines((BASKETBALL / filename).read_bytes()):
            if row["league"] != "NBA":
                continue
            key = row["payload"]["source_fixture_id"]
            if row["kind"] == "fixture":
                fixtures[key] = row
            elif row["kind"] == "result":
                results.add(key)
            elif row["kind"] == "lineup" and injury_before_tip(row):
                injuries[key] = row
    if injury_pdf:
        raw = gzip.decompress(injury_pdf.read_bytes())
        if not raw.startswith(b"%PDF") or {hashlib.sha256(raw).hexdigest()} != {
                row["body_sha256"] for row in injuries.values()}:
            raise ValueError("NBA injury report archive digest mismatch")
    for key, row in injuries.items():
        fixture = fixtures[key]
        if row["payload"]["fixture_record_id"] != fixture["record_id"] or (
                row["kickoff"], row["home"], row["away"]) != (fixture["kickoff"], fixture["home"], fixture["away"]):
            raise ValueError(f"NBA injury report fixture mismatch: {key}")

    prices = Counter()
    price_rules = Counter()
    for row in jsonlines(gzip.decompress(PRICES.read_bytes())):
        if row["league"] != "NBA":
            continue
        payload = row["payload"]
        identity = payload["source_identity"]
        prices[(datetime.fromisoformat(row["kickoff"]), identity["homeId"], identity["awayId"])] += 1
        price_rules[payload["settlement_rule"]] += 1

    keys = {key: fixture_key(row) for key, row in fixtures.items()}
    for key, box in boxes.items():
        match = datetime.fromisoformat(box["tip"]), box["home"], box["away"]
        if keys.setdefault(key, match) != match:
            raise ValueError(f"NBA box and fixture identity disagree: {key}")
    paired_prices = {key for key, match in keys.items() if prices[match] == 1}
    prior_boxes = set()
    for key in injuries:
        tip, home, away = keys[key]
        recent_teams = {team for box in boxes.values()
                        if timedelta(0) < tip - datetime.fromisoformat(box["tip"]) <= timedelta(days=7)
                        for team in box["teams"]}
        if {home, away} <= recent_teams:
            prior_boxes.add(key)
    candidates = prior_boxes & paired_prices & (results | boxes.keys())
    if candidates:
        raise ValueError("NBA archive contains new candidates requiring schedule, price-rule and baseline checks")
    return {
        "official_schedule_fixtures": len(fixtures),
        "official_final_results_in_schedule_archive": len(results),
        "official_final_boxes_with_starter_minutes": boxes,
        "pregame_injury_fixtures": len(injuries),
        "pregame_injury_player_status_rows": sum(len(row["payload"]["players"]) for row in injuries.values()),
        "pregame_injury_pdf_verified_against_archive": bool(injury_pdf),
        "injury_fixtures_with_any_box_for_both_teams_in_prior_7d": len(prior_boxes),
        "archived_proto_price_rows": sum(prices.values()),
        "archived_proto_price_rules": dict(price_rules),
        "official_fixtures_with_one_exact_quote_by_time_and_team_codes": len(paired_prices),
        "injury_fixtures_with_one_exact_quote_by_time_and_team_codes": len(paired_prices & injuries.keys()),
        "eligible_games": len(candidates),
        "status": "blocked_data",
        "metrics": None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--injury-pdf", type=Path, help="Original compressed PDF body from the 2026-11-04 capture")
    args = parser.parse_args()
    result = audit(args.state, args.injury_pdf)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
