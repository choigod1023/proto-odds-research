"""Small, independent KBO pregame feed; no historical/player dataset loading.

Never fabricate batting orders from candidate rosters. Failures retain the last
successful observation and its timestamp; the next supervisor cycle retries.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import sys
import requests

from baseball_context_watch import API, KST, normalize_starter, normalize_lineup, _extract_lineup
from runtime_db import load_document, persist_document

OUT = Path(__file__).resolve().parent.parent / "data/raw/kbo_pregame.json"


def source_session():
    """Use the same public-source headers as the other Naver collectors."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; proto-odds-research/1.0)",
        "Accept": "application/json", "Referer": "https://m.sports.naver.com/",
    })
    return session


def collect(session=None, now=None, existing=None):
    if session is None:
        with source_session() as owned_session:
            return collect(owned_session, now, existing)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    existing = existing if existing is not None else load_document("kbo_pregame", OUT) or {}
    previous = {r["game_id"]: r for r in existing.get("games", [])}
    day = now.astimezone(KST).date()
    response = session.get(API, params={
        "fields": "basic,statusNum,homeStarterName,awayStarterName",
        "upperCategoryId": "kbaseball", "categoryId": "kbo",
        "fromDate": day.isoformat(), "toDate": (day + timedelta(days=1)).isoformat(), "size": 30,
    }, timeout=15)
    response.raise_for_status()
    payload = response.json()
    games = payload.get("result", {}).get("games")
    if not isinstance(games, list):
        raise ValueError("KBO schedule missing games; preserve previous document")
    if not games and any(datetime.fromisoformat(r['game_datetime']) > now
                         for r in previous.values() if r.get('game_datetime')):
        raise ValueError("Empty schedule contradicts saved upcoming games; retry")
    records, failures = [], 0
    for game in games:
        if game.get("statusCode") != "BEFORE" or not game.get("gameId"):
            continue
        gid = game["gameId"]
        old = previous.get(gid, {})
        record = {"game_id": gid, "league": "KBO", "sport": "bs",
                  "home_team": game["homeTeamName"], "away_team": game["awayTeamName"],
                  "game_datetime": game["gameDateTime"] + ("+09:00" if len(game["gameDateTime"]) == 19 else ""),
                  "updated_at": stamp, "source": "네이버 스포츠 공개 경기정보",
                  "source_url": f"https://m.sports.naver.com/game/{gid}/preview",
                  "starters": {}, "lineups": {}, "starter_status": {"state": "announced"}}
        for side in ("home", "away"):
            name = game.get(side + "StarterName")
            if name:
                record["starters"][side] = {"name": name, "announced": True}
        preview, lineup, errors = {}, {}, []
        # Independent requests: preview failure must not suppress lineup polling.
        for endpoint, field in (("preview", "previewData"), ("lineup", "lineUpData")):
            try:
                r = session.get(f"{API}/{gid}/{endpoint}", timeout=15)
                r.raise_for_status()
                body = r.json()
                if not isinstance(body.get("result"), dict):
                    raise ValueError("missing result")
                value = body["result"].get(field) or {}
                if endpoint == "preview":
                    preview = value
                else:
                    lineup = _extract_lineup(value) or {}
            except (requests.RequestException, ValueError, TypeError):
                errors.append(endpoint)
        side_states = {}
        for side in ("home", "away"):
            pitcher = normalize_starter(preview.get(side + "Starter"), game.get(side + "StarterName"))
            if pitcher and pitcher.get("name"):
                stats = pitcher.get("season") or {}
                record["starters"][side] = {"name": pitcher["name"], "announced": True,
                    "stats": {"era": stats.get("era"), "whip": stats.get("whip"), "period": "시즌"}}
            raw = lineup.get(side) or preview.get(side + "TeamLineUp") or {}
            normalized = normalize_lineup(raw)
            if normalized["confirmed"]:
                record["lineups"][side] = sorted(
                    [p for p in normalized["players"] if str(p.get("batting_order")) in set(map(str, range(1,10)))],
                    key=lambda p: int(p["batting_order"]))
                side_states[side] = "official_today"
            else:
                side_states[side] = "fetch_failed" if errors else "not_announced"
                if old.get("lineups", {}).get(side):
                    record["lineups"][side] = deepcopy(old["lineups"][side])
                    side_states[side] = "stale"
            if not record["starters"].get(side) and old.get("starters", {}).get(side):
                record["starters"][side] = deepcopy(old["starters"][side])
                record["starters"][side]["stale"] = True
        official = all(s == "official_today" for s in side_states.values())
        record["lineup_status"] = {"state": "official_today" if official else "pending",
            "official_today": official, "side_states": side_states,
            "label": "오늘 발표 선발 타순" if official else "선발 타순 확인 중",
            "observed_at": stamp if official else (old.get("lineup_status") or {}).get("observed_at"),
            "caveat": "수집 실패 — 자동 재시도 중" if errors else "원천에 타순 미발표 — 자동 재조회 중"}
        record["collection"] = {"checked_at": stamp, "failed_endpoints": errors}
        failures += len(errors)
        records.append(record)
    return {"generated_at": stamp, "games": records, "errors": failures}


def current_document():
    return load_document("kbo_pregame", OUT) or {"games": []}


def display_info(record):
    record = deepcopy(record)
    starters = record.get("starters") or {}
    return {**{side: starters.get(side, {}).get("name") for side in ("home", "away")},
            **{side + "_detail": starters.get(side) for side in ("home", "away")},
            **{k: record.get(k) for k in ("lineups", "lineup_status", "starter_status", "updated_at", "source", "source_url", "game_id")}}


def matching_record(game, document, now=None):
    """Exact date/time/team match; do not mix a doubleheader or yesterday's roster."""
    now = now or datetime.now(timezone.utc)
    m = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", str(game.get("date", "")))
    if game.get("league") != "KBO" or not m:
        return None
    try:
        kickoff = datetime(int(game.get("year") or now.astimezone(KST).year), *map(int, m.groups()), tzinfo=KST)
        rows = [r for r in document.get("games", []) if r.get("league") == "KBO"
                and r.get("home_team") == game.get("home") and r.get("away_team") == game.get("away")
                and datetime.fromisoformat(r["game_datetime"]) == kickoff]
        if len(rows) != 1:
            return None
        age = now - datetime.fromisoformat(rows[0]["updated_at"])
        return rows[0] if timedelta(0) <= age <= timedelta(hours=6) else None
    except (KeyError, ValueError, TypeError):
        return None


def collect_available(now=None, existing=None):
    """Prefer the official route verified from Tokyo; retain Naver as fallback."""
    from kbo_official import collect_official
    existing = existing if existing is not None else current_document()
    try:
        return collect_official(now=now, existing=existing)
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
        print(json.dumps({'route': 'kbo_official', 'status': 'fallback',
                          'error_type': type(exc).__name__}), flush=True)
        doc = collect(now=now, existing=existing)
        doc['route'] = 'naver_fallback'
        return doc


def main():
    try:
        doc = collect_available()
    except (requests.RequestException, ValueError, TypeError) as exc:
        # Do not publish an empty/successful document or advance observation time.
        # The supervisor owns the bounded-interval retry; no recursive retries.
        print(json.dumps({"status": "fetch_failed", "endpoint": "schedule",
                          "error_type": type(exc).__name__, "preserved_previous": True}), flush=True)
        return 1
    persist_document("kbo_pregame", doc, OUT)
    print(json.dumps({"kbo_games": len(doc["games"]), "errors": doc["errors"], "generated_at": doc["generated_at"], "route": doc.get('route')}), flush=True)
    return 1 if doc['errors'] else 0


if __name__ == "__main__":
    sys.exit(main())
