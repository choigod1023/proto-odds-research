"""Find an observed lineup revision and address its separate M0 capture."""
from datetime import timedelta
import json

from . import ledger
from .lineup_revisions import BAD_SOURCE, CUTOFF, MAX_GAP, confirmed_snapshot, read_body
from .official import LINEUP, SCHEDULE, announced_starters, lineup_params

CAPTURE_DIR = "lineup-m0-captures"


def revision_identity(event, lineup):
    return {"season": event["season"], "official_game_id": event["official_game_id"],
            "received_at": lineup["received_at"], "raw_sha256": lineup["raw_sha256"]}


def capture_path(state, revision, market_id):
    key = ledger.digest(ledger.canonical([revision, market_id]))
    return state / CAPTURE_DIR / (key + ".jsonl")


def revision_anchor(official, game, receipt, as_of):
    """Return the first observation of the current contiguous changed lineup.

    Match #612's latest-schedule and consecutive-confirmed-receipt rules.
    Only inspect this fixture in the runner's three-hour observation window.
    """
    source = game["source"]
    end = ledger.moment(receipt["finished_at"])
    start = as_of - timedelta(hours=3) - MAX_GAP
    if end > as_of:
        return None
    schedules, lineups = {}, {}
    for rec in official.records:
        params = rec.get("params") or {}
        if rec["url"] == SCHEDULE and params.get("date") == source["G_DT"]:
            bucket = schedules
        elif (rec["url"] == LINEUP and params.get("gameId") == source["G_ID"]
              and str(params.get("seasonId")) == str(source["SEASON_ID"])):
            bucket = lineups
        else:
            continue
        at = ledger.moment(rec["finished_at"])
        if bucket is lineups and end < at <= as_of:
            return None
        if start <= at <= end:
            bucket[at] = rec if at not in bucket else None
    if lineups.get(end) != receipt:
        return None

    current = None
    for at in sorted(lineups, reverse=True):
        rec = lineups[at]
        try:
            requested = ledger.moment(rec["requested_at"])
            prior = max(t for t in schedules if t <= requested)
            schedule = schedules[prior]
            if str(schedule["params"]["leId"]) != "1":
                return None
            raw = read_body(official.root / "bodies", schedule, prior, as_of, ".bin")
            data = json.loads(raw)
            games = {g["G_ID"]: g for g in data["game"]}
            if (str(data["code"]) != "100" or len(games) != len(data["game"])
                    or any(str(g["LE_ID"]) != "1" or g["G_DT"] != source["G_DT"] for g in games.values())):
                return None
            scheduled = games[source["G_ID"]]
            event, signature, snapshot = confirmed_snapshot(
                official.root, rec, at, (schedule, scheduled), as_of
            )
            if (lineup_params(scheduled) != lineup_params(source)
                    or ledger.moment(event["kickoff_at"]) - CUTOFF < end):
                return None
            if at == end and announced_starters(scheduled) != announced_starters(source):
                return None
        except BAD_SOURCE:
            return None
        if current is not None:
            newer_event, newer_signature, newer_snapshot = current
            if event != newer_event:
                return None
            if signature != newer_signature:
                return revision_identity(newer_event, newer_snapshot["lineup"])
        current = event, signature, snapshot
    return None
