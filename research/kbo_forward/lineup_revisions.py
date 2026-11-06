"""Read-only replay of confirmed KBO lineup revisions and observed Betman odds.

Run with python -m research.kbo_forward.lineup_revisions --state <collector state>.
Uses local direct receipt logs and their hash-addressed bodies. Recorded prices
must bracket the two lineup receipts with no missing or different price in between
and at most ten minutes between successive observations. This measures observed
prices only. Batter names and slots are taken as published by KBO. The linked
result is the official KBO final score, separate from Betman product settlement.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from .official import (
    KST, LINEUP, SCHEDULE, SourceError, announced_starters, lineup_params,
    parse_lineup, result_from_schedule, sha,
)
from .prices import DETAIL_URL, LIST_URL, TEAMS, _millis, _moment, parse_detail

MAX_GAP = timedelta(minutes=10)
CUTOFF = timedelta(minutes=30)
BAD_SOURCE = (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, OverflowError, SourceError)


def receipts(path):
    if path.is_file():
        with path.open(encoding="utf8") as stream:
            yield from (json.loads(line) for line in stream)


def read_body(folder, record, completed_at, as_of, suffix):
    digest = record["sha256"]
    if (record.get("method") != "POST" or record.get("status") != 200 or record.get("truncated")
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not _moment(record["requested_at"]) <= completed_at <= as_of):
        raise ValueError("invalid direct receipt")
    raw = (folder / (digest + suffix)).read_bytes()
    if sha(raw) != digest:
        raise ValueError("raw body hash mismatch")
    return raw


def evidence(record, completion_field):
    return {"url": record["url"], "params": record["params"],
            "requested_at": record["requested_at"],
            "received_at": record[completion_field], "raw_sha256": record["sha256"]}


def fixture(game):
    start = datetime.strptime(game["G_DT"] + " " + game["G_TM"], "%Y%m%d %H:%M").replace(tzinfo=KST)
    home, away, season = game["HOME_ID"], game["AWAY_ID"], int(game["SEASON_ID"])
    if (home not in TEAMS or away not in TEAMS or home == away or season != start.year
            or not isinstance(game["G_ID"], str) or not game["G_ID"].strip()):
        raise ValueError("invalid KBO fixture")
    return {"official_game_id": game["G_ID"], "season": season, "home_id": home,
            "away_id": away, "kickoff_at": start.isoformat()}


def official_index(root, as_of, counts):
    schedules, lineups = defaultdict(dict), defaultdict(dict)
    fixtures, days_by_id = {}, defaultdict(set)
    for record in receipts(root / "receipts.jsonl"):
        url = record.get("url")
        if url not in (SCHEDULE, LINEUP):
            continue
        counts["schedule_receipts" if url == SCHEDULE else "lineup_receipts"] += 1
        bucket = None
        try:
            at = _moment(record["finished_at"])
            if url == LINEUP:
                params = record["params"]
                bucket = lineups[(int(params["seasonId"]), params["gameId"])]
                bucket[at] = record if at not in bucket else None
                continue
            day = record["params"]["date"]
            if str(record["params"]["leId"]) != "1":
                raise ValueError("not KBO")
            bucket = schedules[day]
            raw = read_body(root / "bodies", record, at, as_of, ".bin")
            data = json.loads(raw)
            games = {row["G_ID"]: row for row in data["game"]}
            if (str(data["code"]) != "100" or len(games) != len(data["game"])
                    or any(str(row["LE_ID"]) != "1" or row["G_DT"] != day for row in games.values())):
                raise ValueError("invalid schedule")
            bucket[at] = (record, games) if at not in bucket else None
            for game in games.values():
                try:
                    event = fixture(game)
                except BAD_SOURCE:
                    continue
                key = (event["season"], event["official_game_id"])
                days_by_id[key].add(day)
                fixtures[tuple(event.values())] = event
        except BAD_SOURCE:
            counts["invalid_official_receipts"] += 1
            if bucket is not None:
                # A corrupt response for this date must not let an older row win.
                bucket[at] = None
    return schedules, lineups, days_by_id, list(fixtures.values())


def confirmed_snapshot(root, record, at, schedule, as_of):
    if record is None or schedule is None:
        raise ValueError("ambiguous or missing receipt")
    schedule_record, game = schedule
    event = fixture(game)
    start = _moment(event["kickoff_at"])
    if (at > start - CUTOFF or at - _moment(schedule_record["finished_at"]) > MAX_GAP
            or lineup_params(game) != record["params"]
            or str(game.get("GAME_STATE_SC")) != "1" or str(game.get("CANCEL_SC_ID")) != "0"):
        raise ValueError("not a matching pregame fixture")
    raw = read_body(root / "bodies", record, at, as_of, ".bin")
    players = parse_lineup(raw, game)
    starters = announced_starters(game)
    if (players is None or len(starters) != 2
            or any(len({p["name"] for p in team}) != 9 for team in players.values())):
        raise ValueError("complete confirmed lineup missing")
    signature = tuple(
        (starters[side]["player_id"], tuple(p["name"] for p in players[side]))
        for side in ("home", "away")
    )
    return event, signature, {
        "starters": starters, "batting_order": players, "lineup": evidence(record, "finished_at"),
        "schedule": evidence(schedule_record, "finished_at"),
    }


def price_frames(state, fixtures, as_of, counts):
    """Each listing starts a collection cycle. Missing/failed rounds are gaps."""
    frames = []
    archive = state / "prices/price-evidence"
    for record in receipts(state / "price-request-receipts.jsonl"):
        url = record.get("url")
        if url not in (LIST_URL, DETAIL_URL):
            continue
        counts["price_receipts"] += 1
        try:
            requested = _moment(record["requested_at"])
            if url == LIST_URL:
                frame = {"started": requested, "rounds": {}, "details": {}}
                frames.append(frame)
            at = _moment(record["received_at"])
            raw = read_body(archive, record, at, as_of, ".body")
            if "json" not in record["content_type"].lower():
                raise ValueError("non-JSON receipt")
            if url == LIST_URL:
                rounds = [r for r in json.loads(raw)["protoGames"] if r.get("gmId") == "G101"]
                indexed = {str(r["gmTs"]): r for r in rounds}
                if len(indexed) != len(rounds):
                    raise ValueError("duplicate listing round")
                frame.update(rounds=indexed, at=at, listing=evidence(record, "received_at"))
                continue
            params = record["params"]
            round_id = params["gmTs"]
            if not frames or round_id in frame["details"]:
                if frames:
                    frame["details"][round_id] = (requested, {})
                raise ValueError("missing listing or duplicate round detail")
            info = frame["rounds"][round_id]
            if (frame["at"] > requested or at - frame["at"] > MAX_GAP
                    or params["gmId"] != "G101" or params["gameYear"] != str(info["gmOsidTsYear"])
                    or not re.fullmatch(r"[0-9]{6}", round_id)
                    or int(round_id[:2]) != info["gmOsidTsYear"] % 100
                    or ("saleStartDate" in info and _millis(info["saleStartDate"]) > at)):
                raise ValueError("round receipt identity or time mismatch")
            sources = {"detail": evidence(record, "received_at"), "listing": frame["listing"]}
            quotes, _ = parse_detail(raw, info, fixtures, at.isoformat(), sources, research_exact_lines=True)
            frame["details"][round_id] = (at, {q["market_id"]: q for q in quotes})
        except BAD_SOURCE:
            counts["invalid_price_receipts"] += 1
    return frames


def stable_products(frames, event, before, after):
    market_ids = {
        market_id for frame in frames for _, quotes in frame["details"].values()
        for market_id, quote in quotes.items()
        if all(quote[k] == event[k] for k in ("official_game_id", "home_id", "away_id", "kickoff_at"))
    }
    for market_id in sorted(market_ids):
        _, round_id, _ = market_id.split(":")
        series = []
        for frame in frames:
            at, quotes = frame["details"].get(round_id, (frame["started"], {}))
            series.append((at, quotes.get(market_id)))
        series.sort(key=lambda item: item[0])
        left = max((i for i, (at, _) in enumerate(series) if at <= before), default=-1)
        right = next((i for i, (at, _) in enumerate(series) if at >= after), None)
        if left < 0 or right is None or left >= right:
            continue
        observed = series[left:right + 1]
        if (before - observed[0][0] > MAX_GAP or observed[-1][0] - after > MAX_GAP
                or any(not timedelta(0) < b[0] - a[0] <= MAX_GAP for a, b in zip(observed, observed[1:]))
                or (left > 0 and series[left - 1][0] == observed[0][0])
                or (right + 1 < len(series) and series[right + 1][0] == observed[-1][0])):
            continue
        keys = ("market_id", "official_game_id", "home_id", "away_id", "kickoff_at", "kind", "line",
                "settlement_rule", "source_product", "close_at", "round_close_at", "odds")
        first = observed[0][1]
        if first is None or any(q is None or any(q[k] != first[k] for k in keys) for _, q in observed):
            continue
        # A matchSeq can be reused in later rounds or attached to a different fixture.
        if any(first[k] != event[k] for k in ("official_game_id", "home_id", "away_id", "kickoff_at")):
            continue
        yield {**{k: first[k] for k in keys}, "period": "full_including_extra",
               "observations": [q["evidence"] for _, q in observed]}


def final_result(event, snapshots, pregame, as_of):
    kickoff = _moment(event["kickoff_at"])
    latest = max((at for at in snapshots if kickoff <= at <= as_of), default=None)
    if latest is None or snapshots[latest] is None:
        return None
    record, games = snapshots[latest]
    game = games.get(pregame["G_ID"])
    if game is None or fixture(game) != event:
        return None
    result = result_from_schedule(pregame, {"source": game})
    if result.get("status") != "final" or any(
        not re.fullmatch(r"[0-9]+", str(game.get(key))) for key in ("B_SCORE_CN", "T_SCORE_CN")
    ):
        return None
    return {k: result[k] for k in ("status", "home_score", "away_score")} | {
        "schedule": evidence(record, "finished_at")
    }


def replay(state, as_of=None):
    """Return eligible revision/product rows and source/exclusion counts."""
    state = Path(state)
    if not state.is_dir():
        raise ValueError("collector state directory missing")
    as_of = as_of or datetime.now(timezone.utc)
    counts = Counter(dict.fromkeys((
        "schedule_receipts", "lineup_receipts", "price_receipts", "confirmed_lineup_snapshots",
        "changed_lineups", "without_stable_prices", "without_official_final", "eligible_rows", "eligible_games",
    ), 0))
    schedules, lineups, days_by_id, fixtures = official_index(state / "official", as_of, counts)
    frames = price_frames(state, fixtures, as_of, counts)
    eligible = []
    for key, observations in sorted(lineups.items()):
        days = days_by_id[key]
        if len(days) != 1:
            counts["ambiguous_or_missing_fixture"] += 1
            continue
        schedule = schedules[next(iter(days))]
        previous = None
        for at, rec in sorted(observations.items()):
            try:
                requested = _moment(rec["requested_at"])
                prior = max((t for t in schedule if t <= requested), default=None)
                source, games = schedule[prior]
                game = games[key[1]]
                event, signature, snapshot = confirmed_snapshot(state / "official", rec, at, (source, game), as_of)
                counts["confirmed_lineup_snapshots"] += 1
            except BAD_SOURCE:
                counts["invalid_or_unconfirmed_lineups"] += 1
                previous = None
                continue
            prior_snapshot, previous = previous, (at, event, signature, snapshot, game)
            if prior_snapshot is None:
                continue
            before, prior_event, prior_signature, original, prior_game = prior_snapshot
            if prior_event != event or lineup_params(prior_game) != lineup_params(game) or signature == prior_signature:
                continue
            counts["changed_lineups"] += 1
            markets = list(stable_products(frames, event, before, at))
            if not markets:
                counts["without_stable_prices"] += 1
                continue
            try:
                result = final_result(event, schedule, game, as_of)
            except BAD_SOURCE:
                result = None
            if result is None:
                counts["without_official_final"] += 1
                continue
            eligible.extend({"event": event, "before": original, "after": snapshot,
                             "market": market, "official_result": result} for market in markets)
    counts["eligible_rows"] = len(eligible)
    counts["eligible_games"] = len({(row["event"]["season"], row["event"]["official_game_id"]) for row in eligible})
    return {"schema": "kbo-lineup-revisions-v1", "counts": dict(counts), "rows": eligible}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.state), ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
