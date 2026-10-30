"""User-authorized full-game author plurality. Not a profitable-model claim.

Only first-observed, full-game pregame moneyline votes are admissible. No
network requests, result lookups or fitted parameters in the decision path.
Public full-game adapters supplement MLB votes with live-observed multi-sport
evidence. Missing votes use a market fallback; ties withhold. Vote share is not probability.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from zoneinfo import ZoneInfo

VERSION = "tipster-vote-v1"
HYBRID_VERSION = "market90-tipster10-v2"
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
        return [{"_tipster_load_error": True}]
    try:
        cutoff = stamp(as_of)
        # Date bounds accommodate both Z and +00:00 serialized timestamps.
        since = (cutoff - timedelta(days=3)).date().isoformat()
        through = (cutoff + timedelta(days=1)).date().isoformat()
        rows = []
        db = RuntimeDatabase()
        scanned = 0
        for stream in ("pickster_pick_events", "public_tipster_votes"):
            for row in db.iter_events(stream, since=since, through=through):
                scanned += 1
                if scanned > 20000:
                    return [{"_tipster_load_error": True}]  # Never use a silently truncated electorate.
                if row.get("event_type") == "first_observed":
                    rows.append(row)
        return rows
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return [{"_tipster_load_error": True}]


def context(game, rows, as_of, built_at):
    if game.get("sport") not in {"bs", "bk", "sc", "vl"}:
        return None
    fallback = {"version": VERSION, "status": "withheld", "input_status": "withheld",
                "reason": "no_eligible_pregame_votes", "authors": 0}
    if any(isinstance(row, dict) and row.get("_tipster_load_error") for row in rows or []):
        return dict(fallback, reason="tipster_input_unavailable")
    try:
        match = re.fullmatch(r"(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})", game.get("date", ""))
        kickoff = datetime(int(game["year"]), *map(int, match.groups()), tzinfo=KST)
        cutoff = stamp(as_of)
        if max(cutoff, stamp(built_at)) > kickoff - timedelta(minutes=30):
            return dict(fallback, reason="after_t30")
        home, away = TEAMS.get(game["home"]), TEAMS.get(game["away"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return dict(fallback, reason="unknown_fixture")
    candidates = {}
    from public_tipster_votes import SOURCE, fixture_key, safe_url
    for row in rows or []:
        try:
            if row.get("source") == SOURCE:
                observed = stamp(row["observed_at"])
                if not (row.get("event_type") == "first_observed"
                        and row.get("eligible_pre_event") is True
                        and row.get("provenance") == "live_http_observation"
                        and row.get("market_type") == "full_game_winner"
                        and row.get("fixture") == fixture_key(game)
                        and row.get("side") in {"홈", "무", "원정"}
                        and row.get("voter") and row.get("pick_id")
                        and safe_url(row.get("source_url", ""))
                        and kickoff-timedelta(days=3) <= observed <= min(cutoff, kickoff-timedelta(minutes=30))):
                    continue
                entry = {"id": row["pick_id"], "author": row["voter"], "side": row["side"],
                         "observed_at": observed.isoformat(), "source_url": row["source_url"],
                         "content_hash": row["content_hash"], "published_at": row.get("published_at"),
                         "publication_time_quality": row.get("publication_time_quality")}
                candidates.setdefault(row["voter"], []).append(entry)
                continue
            if game.get("league") != "MLB" or not home or not away:
                continue
            if not (row.get("source") == "tailslips_public_html"
                    and row.get("event_type") == "first_observed" and row.get("identity_version") in {2, 3, 4}
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
            entry = {"id": row["pick_id"], "author": row["slug"], "side": "홈" if pick[1] == home else "원정", "observed_at": observed.isoformat()}
            candidates.setdefault(row["slug"], []).append(entry)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    votes = {}
    for author, entries in candidates.items():
        earliest = min(stamp(e['observed_at']) for e in entries)
        first = [e for e in entries if stamp(e['observed_at']) == earliest]
        if len({e['side'] for e in first}) == 1:
            votes[author] = min(first, key=lambda e:e['id'])
    if not votes:
        # Simultaneous conflicting votes are not evidence of an empty intake.
        if candidates:
            return dict(fallback, reason="ambiguous_author_votes")
        return fallback
    evidence = sorted(votes.values(), key=lambda x: (x["author"], x["id"]))
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    counts = {s:sum(v['side']==s for v in evidence) for s in ('홈','무','원정')}
    leaders = [s for s,n in counts.items() if n == max(counts.values())]
    selected = leaders[0] if len(leaders)==1 else None
    status = 'applied' if selected else 'withheld'
    return {"version": VERSION, "status": status, "input_status": status,
            "reason": "user_authorized_unvalidated" if selected else "tied_tipster_votes",
            "selected_side": selected, "vote_counts": counts,
            "probability_interpretation": "market_reference_not_vote_share",
            "home_vote_share": sum(v["side"] == "홈" for v in evidence)/len(evidence),
            "vote_shares": {s: sum(v["side"] == s for v in evidence)/len(evidence) for s in ("홈", "무", "원정")},
            "authors": len(evidence), "evidence": evidence, "evidence_hash": digest,
            "latest_observed_at": max(stamp(v["observed_at"]) for v in evidence).isoformat()}


def apply(game, info):
    """Select the unique plurality; keep diagnostic market probabilities intact."""
    if info is None:
        return None
    options = [o for o in game.get("options", []) if o.get("market") in {"승패", "승무패"} and not o.get("label")]
    sides = {o.get("선택"): o for o in options}
    order = ("홈", "무", "원정") if "무" in sides else ("홈", "원정")
    try:
        p = [float(sides[s]["시장확률"]) for s in order]
        prices = [float(sides[s]["배당"]) for s in order]
        expected_market = "승무패" if len(order) == 3 else "승패"
        if len(options) != len(order) or set(sides) != set(order) or {o.get("market") for o in options} != {expected_market} or not all(math.isfinite(x) for x in p+prices) or min(p) <= 0 or max(p) >= 1 or abs(sum(p)-1) > .01 or min(prices) <= 1:
            raise ValueError("invalid binary market")
        shares = info.get("vote_shares") or {"홈": info.get("home_vote_share", 0), "원정": 1-info.get("home_vote_share", 0), "무": 0}
        if info["status"] == "applied" and (not all(math.isfinite(shares[s]) and 0 <= shares[s] <= 1 for s in order) or abs(sum(shares[s] for s in order)-1) > 1e-8):
            raise ValueError("invalid vote vector")
    except (KeyError, TypeError, ValueError):
        game["tipster_hybrid"] = dict(info, status="withheld", reason="invalid_full_game_moneyline", input_reason=info.get("reason"))
        return None
    game["tipster_hybrid"] = info
    game["probability_pipeline"]["selection_policy"] = VERSION
    market_fallback = (info.get('status') == 'withheld'
                       and info.get('reason') == 'no_eligible_pregame_votes')
    if info['status'] != 'applied' and not market_fallback:
        return None
    leaders = ([s for i, s in enumerate(order) if p[i] == max(p)] if market_fallback
               else [s for s in order if shares[s] == max(shares[x] for x in order)])
    if len(leaders) != 1:
        game['tipster_hybrid'] = dict(info,status='withheld',reason=('tied_market_probabilities' if market_fallback else 'tied_tipster_votes'),input_reason=info.get('reason'),selected_side=None)
        return None
    selected = leaders[0]
    game['tipster_hybrid'] = (dict(info, status='applied', input_status='withheld',
                                     reason='market_fallback_no_votes', input_reason=info.get('reason'), selection_source='market_fallback',
                                     selected_side=selected) if market_fallback else dict(info, selected_side=selected))
    for i, side in enumerate(order):
        option = sides[side]
        probability = p[i]
        option.update({"최종확률": probability, "AI반영": False,
                       "확률근거": "shin_market",
                       "예상적중확률": round(probability, 4), "추천점수": round(probability, 4),
                       "선택근거": "market_fallback" if market_fallback else VERSION,
                       "추천우선순위": "market_fallback" if market_fallback else "tipster_vote"})
        if market_fallback:
            option.pop("픽스터지지율", None)
        else:
            option["픽스터지지율"] = shares[side]
        option.pop("제외", None)
    game["probability_pipeline"].update(operating_version=VERSION, policy_authorized=True,
        status="operational", affects_probability=False, affects_selection=True,
        reason="user_authorized_unvalidated", artifact_hash=None, allowlisted=False)
    return sides[selected]
