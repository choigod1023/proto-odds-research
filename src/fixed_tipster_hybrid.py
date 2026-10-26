"""User-authorized full-game 90:10 policy. Not a validated profitable model.

Only first-observed, full-game pregame moneyline votes are admissible. No
network requests, result lookups or fitted parameters in the decision path.
Only MLB currently has a verified vote adapter; other sports use an explicitly
labelled market-only fallback, never fabricated or retrospective tipster votes.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from zoneinfo import ZoneInfo

VERSION = "market90-tipster10-v2"
LEGACY_VERSION = "mlb-market90-tipster10-v1"
TEAMS = dict(zip(
    "LA다저스 LA에인절 뉴욕메츠 뉴욕양키 디트타이 마이말린 미네트윈 밀워브루 보스레드 볼티오리 샌디파드 샌프자이 세인카디 시애매리 시카컵스 시카화이 신시레즈 애리다이 애슬레틱 애틀브레 워싱내셔 캔자로얄 콜로로키 클리가디 탬파레이 텍사레인 토론블루 피츠파이 필라필리 휴스애스".split(),
    "LAD LAA NYM NYY DET MIA MIN MIL BOS BAL SD SF STL SEA CHC CWS CIN ARI ATH ATL WSH KC COL CLE TB TEX TOR PIT PHI HOU".split()))
KST = timezone(timedelta(hours=9))


def stamp(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.utcoffset() is None:
        raise ValueError("timezone required")
    return value


def load_votes(as_of):
    """Bound SQL history and Python memory; any failure retains baseline."""
    from runtime_db import RuntimeDatabase, database_enabled
    if not database_enabled():
        return []
    try:
        cutoff = stamp(as_of)
        # Date bounds accommodate both Z and +00:00 serialized timestamps.
        since = (cutoff - timedelta(days=3)).date().isoformat()
        through = (cutoff + timedelta(days=1)).date().isoformat()
        rows = []
        for row in RuntimeDatabase().iter_events("pickster_pick_events", since=since, through=through):
            if len(rows) >= 20000:
                return []  # Never use a silently truncated electorate.
            if row.get("event_type") == "first_observed":
                rows.append(row)
        return rows
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return []


def context(game, rows, as_of, built_at):
    if game.get("sport") not in {"bs", "bk", "sc", "vl"}:
        return None
    fallback = {"version": VERSION, "status": "market_fallback", "reason": "no_eligible_pregame_votes"}
    if game.get("league") != "MLB":
        return dict(fallback, reason="no_verified_tipster_adapter")
    try:
        match = re.fullmatch(r"(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})", game.get("date", ""))
        kickoff = datetime(int(game["year"]), *map(int, match.groups()), tzinfo=KST)
        cutoff = stamp(as_of)
        if max(cutoff, stamp(built_at)) > kickoff - timedelta(minutes=30):
            return dict(fallback, reason="after_t30")
        home, away = TEAMS[game["home"]], TEAMS[game["away"]]
    except (KeyError, TypeError, ValueError, AttributeError):
        return dict(fallback, reason="unknown_fixture")
    votes = {}
    for row in rows or []:
        try:
            if not (row.get("source") == "tailslips_public_html"
                    and row.get("event_type") == "first_observed" and row.get("identity_version") == 2
                    and row.get("eligible_pre_event") is True and row.get("game_started") is False
                    and row.get("result") is None and not row.get("is_parlay_leg")
                    and not row.get("is_baseline") and row.get("market_type") == "moneyline"
                    and row.get("slug") and row.get("pick_id")):
                continue
            if {row.get("team_a"), row.get("team_b")} != {home, away}:
                continue
            selection = re.sub(r"\s+\d+(?:\.\d+)?u$", "", row["selection"], flags=re.I)
            pick = re.fullmatch(r"([A-Z]+) ML ([+-]\d{3,4})", selection)
            if not pick or pick[1] not in (home, away) or abs(int(pick[2])) < 100 or row.get("american_odds") != int(pick[2]):
                continue
            clock = re.search(r"\b(\d{1,2}:\d{2})\s+(AM|PM)\s+ET\b", row["game_header"])
            scheduled = datetime.strptime(row["slate_date"] + " " + clock[1] + " " + clock[2], "%Y-%m-%d %I:%M %p").replace(tzinfo=ZoneInfo("America/New_York"))
            observed = stamp(row["observed_at"])
            if abs((scheduled-kickoff).total_seconds()) > 600 or observed > min(cutoff, scheduled-timedelta(minutes=30)):
                continue
            if observed < kickoff-timedelta(days=3):
                continue
            entry = {"id": row["pick_id"], "author": row["slug"], "side": pick[1], "observed_at": observed.isoformat()}
            old = votes.get(row["slug"])
            if old is None or (observed, entry["id"]) < (stamp(old["observed_at"]), old["id"]):
                votes[row["slug"]] = entry
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    if not votes:
        return fallback
    evidence = sorted(votes.values(), key=lambda x: (x["author"], x["id"]))
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {"version": VERSION, "status": "applied", "reason": "user_authorized_unvalidated",
            "home_vote_share": sum(v["side"] == home for v in evidence)/len(evidence),
            "authors": len(evidence), "evidence": evidence, "evidence_hash": digest,
            "latest_observed_at": max(stamp(v["observed_at"]) for v in evidence).isoformat()}


def apply(game, info):
    """Return full-game winner; ignore legacy odds priority and favorite gate."""
    if info is None:
        return None
    options = [o for o in game.get("options", []) if o.get("market") in {"승패", "승무패"} and not o.get("label")]
    sides = {o.get("선택"): o for o in options}
    order = ("홈", "무", "원정") if "무" in sides else ("홈", "원정")
    try:
        p = [float(sides[s]["시장확률"]) for s in order]
        prices = [float(sides[s]["배당"]) for s in order]
        if len(options) != len(order) or set(sides) != set(order) or len({o.get("market") for o in options}) != 1 or not all(math.isfinite(x) for x in p+prices) or min(p) <= 0 or max(p) >= 1 or abs(sum(p)-1) > .01 or min(prices) <= 1 or (info["status"] == "applied" and len(order) != 2):
            raise ValueError("invalid binary market")
    except (KeyError, TypeError, ValueError):
        game["tipster_hybrid"] = dict(info, status="withheld", reason="invalid_full_game_moneyline")
        return None
    game["tipster_hybrid"] = info
    game["probability_pipeline"]["selection_policy"] = VERSION
    applied = info["status"] == "applied"
    for i, side in enumerate(order):
        option = sides[side]
        probability = p[i]
        if applied:
            probability /= sum(p)
            vote = info["home_vote_share"] if i == 0 else 1-info["home_vote_share"]
            probability = .9*probability + .1*vote
        option.update({"최종확률": round(probability, 4), "AI반영": applied,
                       "확률근거": VERSION if applied else "shin_market",
                       "예상적중확률": round(probability, 4), "추천점수": round(probability, 4),
                       "선택근거": "user_authorized_single_moneyline", "추천우선순위": "single"})
        option.pop("제외", None)
    if applied:
        game["probability_pipeline"].update(operating_version=VERSION, policy_authorized=True,
            status="operational", affects_probability=True, reason="user_authorized_unvalidated",
            artifact_hash=None, allowlisted=False)
    return max((sides[s] for s in order), key=lambda o: o["최종확률"])
