"""KBO·NPB·MLB 경기 직전 무료 컨텍스트 관측기.

네이버 스포츠 공개 페이지가 사용하는 keyless JSON에서 선발, 최근 팀 경기, 선발의
시즌/상대 성적, 구종, 타선 공개 상태를 작게 정규화한다. 원 응답 전체를 복제하지 않고
변경된 스냅샷만 append-only로 저장한다.

NPB는 현재 프리뷰 상세가 비는 경우가 많다. 그때도 일정·선발 발표/교체는 남기며,
없는 값을 0으로 채우지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "baseball_context"
LOG = OUT / "events.jsonl"
STATE = OUT / "_state.json"
API = "https://api-gw.sports.naver.com/schedule/games"
KST = timezone(timedelta(hours=9))

TARGETS = [
    ("kbaseball", "kbo", "KBO"),
    ("wbaseball", "npb", "NPB"),
    ("wbaseball", "mlb", "MLB"),
]


def _float(value) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def baseball_innings(value) -> float | None:
    """야구 표기 6.2를 6⅔이닝으로 바꾼다(소수 0.2가 아니다)."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if " " in text and "/" in text:
        whole, frac = text.split(" ", 1)
        a, b = frac.split("/", 1)
        return float(whole) + float(a) / float(b)
    m = re.fullmatch(r"(-?\d+)(?:\.([012]))?", text)
    if m:
        return float(m.group(1)) + (int(m.group(2) or 0) / 3)
    return _float(value)


def _rate(numerator, innings: float | None, scale: float = 9.0) -> float | None:
    n = _float(numerator)
    if n is None or not innings:
        return None
    return round(scale * n / innings, 3)


def _player_name(info: dict) -> str | None:
    return info.get("name") or info.get("playerName") or " ".join(
        x for x in (info.get("firstName"), info.get("lastName")) if x
    ).strip() or None


def normalize_starter(data: dict | None, fallback_name: str | None = None) -> dict | None:
    if not data and not fallback_name:
        return None
    data = data or {}
    info = data.get("playerInfo") or {}
    stats = data.get("currentSeasonStats") or {}
    opp = data.get("currentSeasonStatsOnOpponents") or {}
    recent = data.get("latelyGamePitcherStat") or {}
    innings = baseball_innings(stats.get("inn") or stats.get("inn2") or stats.get("ip"))
    k = _float(stats.get("kk") if "kk" in stats else stats.get("so"))
    bb = _float(stats.get("bb"))
    hr = _float(stats.get("hr"))
    mix = []
    for p in data.get("currentPitKindStats") or []:
        mix.append({
            "pitch_type": p.get("type"), "usage_pct": _float(p.get("pit_rt")),
            "velocity": _float(p.get("speed")),
        })
    return {
        "player_code": info.get("pCode") or info.get("playerCode"),
        "name": _player_name(info) or fallback_name,
        "throws_bats": info.get("hitType") or info.get("batsThrows"),
        "season": {
            "games": _float(stats.get("gameCount") or stats.get("g")),
            "innings": innings, "era": _float(stats.get("era")),
            "whip": _float(stats.get("whip")), "strikeouts": k, "walks": bb,
            "home_runs": hr,
            "k_minus_bb_per_9": _rate((k - bb) if k is not None and bb is not None else None, innings),
            "hr_per_9": _rate(hr, innings),
        },
        "vs_opponent": {
            "games": _float(opp.get("gameCount")), "innings": baseball_innings(opp.get("inn")),
            "era": _float(opp.get("era")), "strikeouts": _float(opp.get("kk")),
            "walks": _float(opp.get("bb")),
        } if opp else None,
        "recent": recent or None,
        "pitch_mix": mix,
    }


def _walk_players(value, out: list[dict]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_players(item, out)
        return
    if not isinstance(value, dict):
        return
    name = value.get("playerName") or value.get("name") or value.get("firstName")
    code = value.get("playerCode") or value.get("pCode")
    if name and (code or any(k in value for k in ("position", "positionName", "batOrder", "batsThrows"))):
        out.append({
            "player_code": code, "name": name,
            "position": value.get("positionName") or value.get("position"),
            "batting_order": value.get("batOrder") or value.get("battingOrder") or value.get("order"),
            "throws_bats": value.get("batsThrows") or value.get("hitType"),
        })
        return
    for child in value.values():
        if isinstance(child, (dict, list)):
            _walk_players(child, out)


def normalize_lineup(data: object) -> dict:
    players: list[dict] = []
    _walk_players(data, players)
    unique = []
    seen = set()
    for p in players:
        key = p.get("player_code") or (p.get("name"), p.get("position"), p.get("batting_order"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    batters = [p for p in unique if "투수" not in str(p.get("position") or "") and p.get("position") != "P"]
    ordered = [p for p in batters if p.get("batting_order") not in (None, "", 0, "0")]
    confirmed = len(ordered) >= 9 or len(batters) >= 9
    return {
        "confirmed": confirmed, "player_count": len(unique), "batter_count": len(batters),
        "players": unique[:30],
    }


def _recent_games(rows: list[dict], team_code: str | None) -> dict | None:
    if not rows:
        return None
    wins = runs_for = runs_against = 0
    valid = 0
    dates = []
    for g in rows:
        if team_code and g.get("hCode") == team_code:
            rf, ra = _float(g.get("hScore")), _float(g.get("aScore"))
        elif team_code and g.get("aCode") == team_code:
            rf, ra = _float(g.get("aScore")), _float(g.get("hScore"))
        else:
            rf = ra = None
        if rf is not None and ra is not None:
            valid += 1
            runs_for += rf
            runs_against += ra
            wins += int(rf > ra)
        if g.get("gdate"):
            dates.append(str(g["gdate"]))
    return {
        "games": valid, "wins": wins, "runs_for": runs_for,
        "runs_against": runs_against, "latest_game_date": max(dates) if dates else None,
    }


def _top_hitter(data: dict | None) -> dict | None:
    if not data:
        return None
    info = data.get("playerInfo") or {}
    season = data.get("currentSeasonStats") or {}
    recent = data.get("recentFiveGamesStats") or {}
    opp = data.get("currentSeasonStatsOnOpponents") or {}
    return {
        "player_code": data.get("playerCode") or info.get("pCode"), "name": _player_name(info),
        "season_avg": _float(season.get("hra")), "season_obp": _float(season.get("obp")),
        "season_hr": _float(season.get("hr")), "recent_games": _float(recent.get("gameCount")),
        "recent_ab": _float(recent.get("ab")), "recent_avg": _float(recent.get("hra")),
        "recent_obp": _float(recent.get("obp")), "vs_opponent_avg": _float(opp.get("hra")),
    }


def _standings(data: dict | None) -> dict | None:
    if not data:
        return None
    return {
        "rank": _float(data.get("rank")), "wins": _float(data.get("w")),
        "losses": _float(data.get("l")), "ties": _float(data.get("d")),
        "win_rate": _float(data.get("wra")), "team_avg": _float(data.get("hra")),
        "team_era": _float(data.get("era")), "home_runs": _float(data.get("hr")),
    }


def normalize_game(game: dict, league: str, preview: dict | None,
                   lineup_data: object | None, observed_at: str, hours_before: float | None) -> dict:
    preview = preview or {}
    info = preview.get("gameInfo") or {}
    away_code = info.get("aCode")
    home_code = info.get("hCode")
    away_lineup_source = (lineup_data or {}).get("away") if isinstance(lineup_data, dict) else None
    home_lineup_source = (lineup_data or {}).get("home") if isinstance(lineup_data, dict) else None
    away_lineup_source = away_lineup_source or preview.get("awayTeamLineUp") or {}
    home_lineup_source = home_lineup_source or preview.get("homeTeamLineUp") or {}
    away_lineup = normalize_lineup(away_lineup_source)
    home_lineup = normalize_lineup(home_lineup_source)
    return {
        "observed_at": observed_at, "source": "naver_sports_public_json",
        "access": "keyless public web data; production terms/licence must be rechecked",
        "game_id": game.get("gameId"), "league": league,
        "game_datetime": game.get("gameDateTime"), "hours_before_game": hours_before,
        "away": game.get("awayTeamName"), "home": game.get("homeTeamName"),
        "generated_date": preview.get("generateDate"),
        "away_features": {
            "starter": normalize_starter(preview.get("awayStarter"), game.get("awayStarterName")),
            "lineup": away_lineup, "standings": _standings(preview.get("awayStandings")),
            "recent_games": _recent_games(preview.get("awayTeamPreviousGames") or [], away_code),
            "featured_hitter": _top_hitter(preview.get("awayTopPlayer")),
        },
        "home_features": {
            "starter": normalize_starter(preview.get("homeStarter"), game.get("homeStarterName")),
            "lineup": home_lineup, "standings": _standings(preview.get("homeStandings")),
            "recent_games": _recent_games(preview.get("homeTeamPreviousGames") or [], home_code),
            "featured_hitter": _top_hitter(preview.get("homeTopPlayer")),
        },
        "head_to_head": preview.get("seasonVsResult") or None,
        "preview_available": bool(preview),
        "lineup_confirmed": bool(away_lineup["confirmed"] and home_lineup["confirmed"]),
    }


def _state() -> dict:
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        saved = RuntimeDatabase().get_document("baseball_context_state")
        saved = saved or {"games": {}}
        saved.setdefault("games", {})
        return saved
    if not STATE.exists():
        return {"games": {}}
    try:
        out = json.loads(STATE.read_text(encoding="utf-8"))
        out.setdefault("games", {})
        return out
    except (OSError, json.JSONDecodeError):
        return {"games": {}}


def _save(state: dict) -> None:
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        db = RuntimeDatabase()
        db.put_document("baseball_context_state", state,
                        generated_at=state.get("last_success_at"))
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(STATE)


def _append(rows: list[dict]) -> int:
    if not rows:
        return 0
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        db = RuntimeDatabase()
        inserted = db.append_events("baseball_context_events", rows)
        db.export_events("baseball_context_events", LOG)
        return inserted
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def _hash(context: dict) -> str:
    content = {k: v for k, v in context.items() if k not in {"observed_at", "hours_before_game"}}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _game_time(game: dict) -> datetime | None:
    text = game.get("gameDateTime")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.replace(tzinfo=KST).astimezone(timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_lineup(payload: object) -> object | None:
    # 알려진 baseball 응답과 구조가 달라도 원래 dict를 normalize_lineup이 재귀 탐색한다.
    if not isinstance(payload, dict):
        return None
    if "away" in payload or "home" in payload:
        return payload
    for away_key, home_key in (("awayTeamLineUp", "homeTeamLineUp"),
                               ("awayLineUp", "homeLineUp"), ("aLineUp", "hLineUp")):
        if away_key in payload or home_key in payload:
            return {"away": payload.get(away_key), "home": payload.get(home_key)}
    return payload


def poll(session: requests.Session, days_ahead: int = 2) -> dict:
    state = _state()
    baseline = not state.get("initialized_at")
    now = datetime.now(timezone.utc)
    observed_at = now.isoformat(timespec="seconds")
    events = []
    checked = errors = 0
    today = date.today()
    for upper, category, league in TARGETS:
        try:
            r = session.get(API, params={
                "fields": "basic,statusNum,homeStarterName,awayStarterName",
                "upperCategoryId": upper, "categoryId": category,
                "fromDate": today.isoformat(),
                "toDate": (today + timedelta(days=days_ahead)).isoformat(), "size": 200,
            }, timeout=25)
            r.raise_for_status()
            games = r.json().get("result", {}).get("games", [])
        except Exception as exc:  # noqa: BLE001
            print(f"[{league}] schedule {type(exc).__name__}: {exc}", flush=True)
            errors += 1
            continue
        for game in games:
            if game.get("statusCode") != "BEFORE" or not game.get("gameId"):
                continue
            gt = _game_time(game)
            hours = round((gt - now).total_seconds() / 3600, 2) if gt else None
            if hours is not None and not (-1 <= hours <= 48):
                continue
            gid = game["gameId"]
            old = state["games"].get(gid, {})
            # 먼 경기는 4시간, 8시간 이내는 30분 간격. supervisor가 30분마다 호출한다.
            interval = 1800 if hours is None or hours <= 8 else 14400
            last_poll = old.get("last_poll")
            if last_poll:
                try:
                    elapsed = (now - datetime.fromisoformat(last_poll)).total_seconds()
                    if elapsed < interval:
                        continue
                except ValueError:
                    pass
            preview = None
            lineup = None
            try:
                q = session.get(f"{API}/{gid}/preview", timeout=25)
                q.raise_for_status()
                preview = q.json().get("result", {}).get("previewData")
                # 실제 lineup endpoint는 경기 12시간 이내에만 추가 조회한다.
                if hours is None or hours <= 12:
                    q = session.get(f"{API}/{gid}/lineup", timeout=25)
                    q.raise_for_status()
                    lineup = _extract_lineup(q.json().get("result", {}).get("lineUpData"))
            except Exception as exc:  # noqa: BLE001
                print(f"[{league} {gid}] detail {type(exc).__name__}: {exc}", flush=True)
                errors += 1
            context = normalize_game(game, league, preview, lineup, observed_at, hours)
            fingerprint = _hash(context)
            away_starter = (context["away_features"].get("starter") or {}).get("name")
            home_starter = (context["home_features"].get("starter") or {}).get("name")
            flags = []
            if old:
                if old.get("away_starter") and away_starter and old["away_starter"] != away_starter:
                    flags.append("away_starter_changed")
                if old.get("home_starter") and home_starter and old["home_starter"] != home_starter:
                    flags.append("home_starter_changed")
                if not old.get("lineup_confirmed") and context["lineup_confirmed"]:
                    flags.append("lineup_confirmed")
                if old.get("fingerprint") != fingerprint and not flags:
                    flags.append("context_changed")
            else:
                flags.append("baseline" if baseline else "first_observed")
            if not old or old.get("fingerprint") != fingerprint:
                events.append({
                    **context, "event_type": flags[0], "change_flags": flags,
                    "is_baseline": baseline and not old,
                    "eligible_pre_event": bool(not baseline and hours is not None and hours > 0),
                })
            state["games"][gid] = {
                "fingerprint": fingerprint, "last_poll": observed_at,
                "away_starter": away_starter, "home_starter": home_starter,
                "lineup_confirmed": context["lineup_confirmed"],
            }
            checked += 1
    written = _append(events)
    state["initialized_at"] = state.get("initialized_at") or observed_at
    state["last_success_at"] = observed_at
    _save(state)
    result = {"observed_at": observed_at, "checked": checked, "events": written,
              "errors": errors, "baseline": baseline}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def _selftest() -> None:
    assert math.isclose(baseball_innings("6.2") or 0, 6 + 2 / 3)
    assert math.isclose(baseball_innings("44 1/3") or 0, 44 + 1 / 3)
    fixture = {
        "awayStarter": {"playerInfo": {"pCode": "1", "name": "A"},
                         "currentSeasonStats": {"inn": "10.0", "kk": 12, "bb": 3,
                                                "hr": 1, "era": "2.70"}},
        "homeTeamLineUp": {"fullLineUp": [
            {"playerCode": str(i), "playerName": f"P{i}", "position": str(i), "batOrder": i}
            for i in range(1, 10)]},
    }
    s = normalize_starter(fixture["awayStarter"])
    assert s and s["season"]["k_minus_bb_per_9"] == 8.1
    lu = normalize_lineup(fixture["homeTeamLineUp"])
    assert lu["confirmed"] and lu["batter_count"] == 9
    assert normalize_lineup({})["confirmed"] is False
    print("✅ baseball_context_watch selftest 통과 (이닝·K-BB/9·라인업 확인)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--days-ahead", type=int, default=2)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; proto-odds-research/1.0)",
        "Accept": "application/json", "Referer": "https://m.sports.naver.com/",
    })
    while True:
        try:
            poll(session, args.days_ahead)
        except Exception as exc:  # noqa: BLE001
            print(f"[baseball_context_watch] {type(exc).__name__}: {exc}", flush=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(max(args.loop, 300))


if __name__ == "__main__":
    raise SystemExit(main())
