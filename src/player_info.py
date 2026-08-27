"""경기별 선수·팀 정보 수집과 결합.

사이트의 선발 이름은 있었지만 `(리그, 홈팀, 원정팀)` 문자열이 완전히 같을 때만
붙었다. 프로토의 축약명(`뉴욕양키`)과 네이버의 정식명(`뉴욕양키스`)이 달라 MLB
예정 경기 전부가 비었다. 이 모듈은 날짜와 검증된 팀 별칭으로 경기를 잇고, 이름에
실제 지표와 출처·갱신 시각을 붙인다.

자료원
  * MLB Stats API: 예정 선발, 시즌 투수 지표, 팀 승패, 40인 로스터 부상 상태
  * 네이버 스포츠 경기기록: KBO 예고 선발
  * NPB.jp 일본야구기구: NPB 예고 선발과 센트럴·퍼시픽 팀 순위
  * 저장된 KBO 박스스코어: 최근 12선발 ERA/FIP/WHIP/K·BB/9/평균 이닝
  * 네이버 스포츠 축구: 현재 시즌 핵심 선수·팀 순위와 경기 직전 실제 라인업
  * FIBA/네이버 농구: 국가대표 등록 명단·선수/팀 통계와 NBA·KBL·WKBL 시즌 기록
  * Volleyball World/네이버 배구: 최근 대표 명단·VNL 기록과 V리그 시즌 기록

사용:
    python src/player_info.py              # 한 번 수집
    python src/player_info.py --loop 1800  # 30분마다
    python src/player_info.py --selftest
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import requests
from availability import enrich_availability
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from court_info import (COURT_SPORTS, collect as collect_court_info,
                        selftest as court_selftest)
from soccer_info import (SOCCER_CATS, collect as collect_soccer_info,
                         selftest as soccer_selftest)
from japan_info import collect_npb_games

from player_commentary import with_player_context
from detail_paths import latest_detail_path
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = RAW / "player_info.json"
PICKS = ROOT / "docs" / "data" / "picks_v2.json"
ANNOUNCEMENTS = RAW / "info_watch" / "starter_announcements.csv"


def kbo_detail_path() -> Path:
    """Resolve the newest cache on every collection cycle."""
    return latest_detail_path("kbo", "baseball")


# Compatibility snapshot for callers that only display the conventional path.
# Runtime reads must call ``kbo_detail_path`` so a long-lived --loop process
# notices the new file after a KST year rollover.
KBO_DETAIL = kbo_detail_path()
TEAM_MAP = ROOT / "data" / "processed" / "team_map.json"
KST = ZoneInfo("Asia/Seoul")
WINDOW = 12
XFIP_SHRINK_IP = 40.0
INJURY_REFRESH_HOURS = 6

MLB_TEAM_KO = {
    108: "LA에인절스", 109: "애리조나", 110: "볼티모어", 111: "보스턴",
    112: "시카고컵스", 113: "신시내티", 114: "클리블랜드", 115: "콜로라도",
    116: "디트로이트", 117: "휴스턴", 118: "캔자스시티", 119: "LA다저스",
    120: "워싱턴", 121: "뉴욕메츠", 133: "애슬레틱스", 134: "피츠버그",
    135: "샌디에이고", 136: "시애틀", 137: "샌프란시스코",
    138: "세인트루이스", 139: "탬파베이", 140: "텍사스", 141: "토론토",
    142: "미네소타", 143: "필라델피아", 144: "애틀랜타", 145: "시카고W",
    146: "마이애미", 147: "뉴욕양키스", 158: "밀워키",
}

# 과거 결과로 만든 team_map 에 아직 표본이 없거나 구단 표기가 바뀐 경우만 보완한다.
EXTRA_ALIAS = {
    "MLB": {
        "애슬레틱": "애슬레틱스", "오클애슬": "애슬레틱스", "오클랜드": "애슬레틱스",
        "뉴욕 양키스": "뉴욕양키스", "세인트 루이스": "세인트루이스",
        "시카고 화이트삭스": "시카고W",
    },
    "NPB": {"소프트뱅": "소프트뱅크", "요코베이": "요코하마", "히로카프": "히로시마"},
}


def _team_map() -> dict:
    try:
        return json.loads(TEAM_MAP.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def canonical_team(league: str, name: str, mapping: dict | None = None) -> str:
    """프로토 축약명과 자료원 정식명을 같은 팀명으로 만든다."""
    league, name = str(league), re.sub(r"\s+", "", str(name or "").strip())
    maps = mapping if mapping is not None else _team_map()
    league_map = maps.get(league, {})
    compact = {re.sub(r"\s+", "", str(k)): v for k, v in league_map.items()}
    canon = compact.get(name, name)
    extra = {re.sub(r"\s+", "", k): v for k, v in EXTRA_ALIAS.get(league, {}).items()}
    return extra.get(re.sub(r"\s+", "", str(canon)), str(canon))


def baseball_innings(value) -> float:
    """`5 1/3`, `5 ⅓`, MLB의 `103.1`(103⅓이닝)을 실제 이닝으로 변환."""
    s = str(value or "").strip().replace("⅓", " 1/3").replace("⅔", " 2/3")
    if not s:
        return 0.0
    if re.fullmatch(r"\d+\.[012]", s):
        whole, outs = s.split(".")
        return int(whole) + int(outs) / 3
    total = 0.0
    for part in s.split():
        try:
            if "/" in part:
                a, b = part.split("/", 1)
                total += float(a) / float(b)
            else:
                total += float(part)
        except (ValueError, ZeroDivisionError):
            continue
    return total


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _kbo_pitcher_identity(pitcher: dict, team: str) -> str | None:
    code = pitcher.get("pcode")
    if code not in (None, ""):
        if isinstance(code, float) and code.is_integer():
            code = int(code)
        return f"pcode:{str(code).strip()}"
    name = str(pitcher.get("name") or "").strip()
    return f"name:{team}:{name}" if name else None


def kbo_pitcher_stats(path: Path | None = None) -> dict[object, dict]:
    """저장된 완료 경기에서 투수별 최근 12선발 과정·결과 지표를 만든다.

    xFIP는 오프라인 실험과 똑같이 실제 피홈런을 리그 HR/9 쪽으로 40이닝
    축소한다. 원자료의 날짜나 핵심 카운팅 지표가 빠진 등판은 0으로 간주하지
    않고 제외해, 불완전한 자료로 좋은 수치를 만들어 내지 않게 한다.
    """
    path = kbo_detail_path() if path is None else path
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    starts: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
    identity_names: dict[str, str] = {}
    name_identities: dict[str, set[str]] = defaultdict(set)
    latest_alias: dict[tuple[str, str], tuple[str, str]] = {}
    all_lines = []
    games = sorted(
        (g for g in raw.values() if isinstance(g, dict)),
        key=lambda g: str(g.get("date") or ""),
    )
    for game in games:
        try:
            game_date = datetime.fromisoformat(
                str(game.get("date") or "").replace("Z", "+00:00")
            ).date().isoformat()
        except ValueError:
            continue
        data = game.get("data") or {}
        if not isinstance(data, dict):
            continue
        for side in ("home", "away"):
            pitchers = data.get(side) or []
            if not pitchers:
                continue
            p = pitchers[0]  # 저장 시 예고 선발과 대조해 검증한 박스스코어 첫 투수
            if not isinstance(p, dict):
                continue
            team = str(game.get(side) or "").strip()
            ip = baseball_innings(p.get("inn"))
            if not p.get("name") or ip <= 0:
                continue
            # 결측을 0실점/0피홈런처럼 해석하면 FIP·xFIP가 과도하게 좋아진다.
            values = []
            for field in ("er", "hr", "bb", "kk", "hit"):
                value = p.get(field)
                try:
                    number = float(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    number = None
                if number is None or not math.isfinite(number) or number < 0:
                    values = []
                    break
                values.append(number)
            if not values:
                continue
            er, hr, bb, so, hit = values
            line = {
                "date": game_date, "ip": ip, "er": er, "hr": hr,
                "bb": bb, "so": so, "hit": hit,
            }
            name = str(p["name"]).strip()
            identity = _kbo_pitcher_identity(p, team)
            if not identity:
                continue
            starts[identity].append(line)
            identity_names[identity] = name
            name_identities[name].add(identity)
            alias = (team, name)
            if alias not in latest_alias or game_date >= latest_alias[alias][0]:
                latest_alias[alias] = (game_date, identity)
            all_lines.append(line)

    totals = {k: sum(x[k] for x in all_lines) for k in ("ip", "er", "hr", "bb", "so")}
    if totals["ip"] <= 0:
        return {}
    # 리그 평균 ERA와 FIP 원값이 같아지게 하는 자료기간 상수.
    fip_c = totals["er"] / totals["ip"] * 9 - (
        13 * totals["hr"] + 3 * totals["bb"] - 2 * totals["so"]
    ) / totals["ip"]
    league_hr9 = totals["hr"] / totals["ip"] * 9

    by_identity = {}
    for identity, lines in starts.items():
        ip = sum(x["ip"] for x in lines)
        if ip <= 0:
            continue
        er, hr, bb, so, hit = (sum(x[k] for x in lines) for k in ("er", "hr", "bb", "so", "hit"))
        weight = ip / (ip + XFIP_SHRINK_IP)
        hr_adjusted = weight * hr + (1 - weight) * (league_hr9 * ip / 9)
        by_identity[identity] = {
            "player_id": (identity.removeprefix("pcode:")
                          if identity.startswith("pcode:") else None),
            "era": round(er / ip * 9, 2),
            "fip": round((13 * hr + 3 * bb - 2 * so) / ip + fip_c, 2),
            "xfip": round((13 * hr_adjusted + 3 * bb - 2 * so) / ip + fip_c, 2),
            "whip": round((hit + bb) / ip, 2),
            "k9": round(so / ip * 9, 2),
            "bb9": round(bb / ip * 9, 2),
            "hr9": round(hr / ip * 9, 2),
            "avg_ip": round(ip / len(lines), 2),
            "sample_ip": round(ip, 2),
            "games_started": len(lines),
            "period": f"최근 {len(lines)}선발",
            "stats_as_of": max(x["date"] for x in lines),
            "low_sample": len(lines) < 4,
            "fip_approx": True,
            "xfip_approx": True,
            "xfip_shrink_ip": XFIP_SHRINK_IP,
            "xfip_league_hr9": round(league_hr9, 4),
        }
    out: dict[object, dict] = {}
    for alias, (_, identity) in latest_alias.items():
        if identity in by_identity:
            out[alias] = by_identity[identity]
    # 기존 호출 호환: 이름이 단 하나의 선수 ID를 뜻할 때만 이름 단독 조회를 허용한다.
    for name, identities in name_identities.items():
        available = [identity for identity in identities if identity in by_identity]
        if len(available) == 1:
            out[name] = by_identity[available[0]]
    return out


def announcement_games() -> list[dict]:
    """필드별 최신 관측을 경기 하나로 합친다. 날짜와 팀 별칭은 여기서 보존한다."""
    if not ANNOUNCEMENTS.exists():
        return []
    mapping, games = _team_map(), {}
    try:
        with ANNOUNCEMENTS.open(newline="", encoding="utf-8") as f:
            rows = csv.DictReader(f)
            for row in rows:
                if row.get("field") not in ("homeStarterName", "awayStarterName"):
                    continue
                gid = row.get("gameId")
                if not gid:
                    continue
                rec = games.setdefault(gid, {
                    "league": row.get("league"), "game_id": gid,
                    "game_datetime": row.get("game_datetime"),
                    "home_team": canonical_team(row.get("league", ""), row.get("home", ""), mapping),
                    "away_team": canonical_team(row.get("league", ""), row.get("away", ""), mapping),
                    "starters": {}, "source": "네이버 스포츠", "source_url": "https://m.sports.naver.com/",
                    "updated_at": row.get("observed_at"),
                })
                side = "home" if row["field"] == "homeStarterName" else "away"
                # CSV가 관측 시각순으로 쌓이므로 마지막 값이 최신 예고다.
                rec["starters"][side] = {"name": row.get("value"), "announced": True}
                if str(row.get("observed_at")) > str(rec.get("updated_at")):
                    rec["updated_at"] = row.get("observed_at")
    except (OSError, csv.Error):
        return []

    kbo_stats = kbo_pitcher_stats()
    for rec in games.values():
        if rec["league"] == "KBO":
            for side, p in rec["starters"].items():
                team = rec.get(f"{side}_team") or ""
                stats = kbo_stats.get((team, p.get("name"))) or kbo_stats.get(p.get("name"))
                if stats:
                    p["stats"] = stats
                    p["stats_source"] = "네이버 경기기록 기반 최근 선발"
    return list(games.values())


def apply_korean_starter_names(games: list[dict], announcements: list[dict]) -> list[dict]:
    """공식 MLB 투수 기록은 유지하고 네이버의 검증된 한글 선발명만 합친다."""
    mapping = _team_map()
    localized = {}
    for rec in announcements:
        key = (
            str(rec.get("league")), _kickoff_minute(rec.get("game_datetime")),
            canonical_team(rec.get("league", ""), rec.get("home_team", ""), mapping),
            canonical_team(rec.get("league", ""), rec.get("away_team", ""), mapping),
        )
        if key[0] != "MLB":
            continue
        current = localized.get(key)
        if current is None or str(rec.get("updated_at") or "") > str(current.get("updated_at") or ""):
            localized[key] = rec
    for game in games:
        key = (
            str(game.get("league")), _kickoff_minute(game.get("game_datetime")),
            canonical_team(game.get("league", ""), game.get("home_team", ""), mapping),
            canonical_team(game.get("league", ""), game.get("away_team", ""), mapping),
        )
        announced = localized.get(key) or {}
        for side, starter in (game.get("starters") or {}).items():
            korean = ((announced.get("starters") or {}).get(side) or {}).get("name")
            if korean and re.search(r"[가-힣]", str(korean)):
                native = starter.get("name")
                if native and native != korean:
                    starter["native_name"] = native
                starter["name"] = korean
    return games


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": "proto-odds-research/1.0", "Accept": "application/json"})
    return s


def _mlb_pitcher_stats(session: requests.Session, ids: set[int], season: int) -> dict[int, dict]:
    if not ids:
        return {}
    r = session.get("https://statsapi.mlb.com/api/v1/people", params={
        "personIds": ",".join(map(str, sorted(ids))),
        "hydrate": f"stats(group=[pitching],type=[season],season={season})",
    }, timeout=30)
    r.raise_for_status()
    out = {}
    for person in r.json().get("people", []):
        splits = ((person.get("stats") or [{}])[0].get("splits") or [])
        stat = (splits[0].get("stat") or {}) if splits else {}
        ip, gs = baseball_innings(stat.get("inningsPitched")), int(stat.get("gamesStarted") or 0)
        out[int(person["id"])] = {
            "era": _num(stat.get("era")) if stat.get("era") not in (None, "-.--") else None,
            "whip": _num(stat.get("whip")) if stat.get("whip") not in (None, "-.--") else None,
            "k9": _num(stat.get("strikeoutsPer9Inn")),
            "bb9": _num(stat.get("walksPer9Inn")),
            "hr9": _num(stat.get("homeRunsPer9")),
            "avg_ip": round(ip / gs, 2) if gs else None,
            "games_started": gs, "wins": int(stat.get("wins") or 0),
            "losses": int(stat.get("losses") or 0), "period": f"{season} 시즌",
            "low_sample": gs < 4,
        }
    return out


def _cached_injuries(existing: dict, now: datetime) -> tuple[dict, bool]:
    stamp = existing.get("injuries_refreshed_at")
    try:
        age = now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if age < timedelta(hours=INJURY_REFRESH_HOURS):
            return existing.get("team_injuries") or {}, True
    except (TypeError, ValueError):
        pass
    return {}, False


def _injury_label(description: str | None) -> str:
    labels = {
        "Injured 7-Day": "7일 부상자 명단", "Injured 10-Day": "10일 부상자 명단",
        "Injured 15-Day": "15일 부상자 명단", "Injured 60-Day": "60일 부상자 명단",
    }
    return labels.get(str(description), str(description or "부상자 명단"))


def _mlb_injuries(session: requests.Session, team_ids: set[int]) -> dict[str, list[dict]]:
    out = {}
    for team_id in sorted(team_ids):
        try:
            r = session.get(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster",
                            params={"rosterType": "40Man", "season": datetime.now(KST).year},
                            timeout=25)
            r.raise_for_status()
            injured = []
            for row in r.json().get("roster", []):
                status = row.get("status") or {}
                if not str(status.get("code") or "").startswith("D"):
                    continue
                injured.append({
                    "name": (row.get("person") or {}).get("fullName"),
                    "status": _injury_label(status.get("description")),
                    "position": (row.get("position") or {}).get("abbreviation"),
                    "reason_code": "injury", "availability_status": "out",
                    "source_type": "official_roster",
                })
            out[str(team_id)] = injured
        except requests.RequestException:
            # 한 팀 오류가 전체 경기 정보까지 지우면 안 된다.
            continue
    return out


def mlb_games(existing: dict | None = None, session: requests.Session | None = None) -> tuple[list[dict], dict, str]:
    existing, session = existing or {}, session or _session()
    now = datetime.now(timezone.utc)
    today = datetime.now(KST).date()
    season = today.year
    r = session.get("https://statsapi.mlb.com/api/v1/schedule", params={
        "sportId": 1, "startDate": (today - timedelta(days=2)).isoformat(),
        "endDate": (today + timedelta(days=7)).isoformat(),
        "hydrate": "probablePitcher,team,lineups",
    }, timeout=30)
    r.raise_for_status()
    schedule = r.json()

    raw_games, pitcher_ids, team_ids = [], set(), set()
    for day in schedule.get("dates", []):
        for game in day.get("games", []):
            teams = game.get("teams") or {}
            if not teams.get("home") or not teams.get("away"):
                continue
            for side in ("home", "away"):
                t = (teams[side].get("team") or {})
                if t.get("id"):
                    team_ids.add(int(t["id"]))
                p = teams[side].get("probablePitcher") or {}
                if p.get("id"):
                    pitcher_ids.add(int(p["id"]))
            raw_games.append(game)
    stats = _mlb_pitcher_stats(session, pitcher_ids, season)

    injuries, cached = _cached_injuries(existing, now)
    if not cached:
        fresh = _mlb_injuries(session, team_ids)
        # 일부 팀 호출만 실패하면 직전 값을 팀 단위로 유지한다.
        injuries = {**(existing.get("team_injuries") or {}), **fresh}
        injury_stamp = now.isoformat(timespec="seconds")
    else:
        injury_stamp = existing.get("injuries_refreshed_at")

    out = []
    for game in raw_games:
        teams = game["teams"]
        rec = {
            "league": "MLB", "game_id": str(game.get("gamePk")),
            "sport": "bs",
            "game_datetime": datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00")).astimezone(KST).isoformat(),
            "home_team": MLB_TEAM_KO.get(int(teams["home"]["team"]["id"]), teams["home"]["team"].get("name")),
            "away_team": MLB_TEAM_KO.get(int(teams["away"]["team"]["id"]), teams["away"]["team"].get("name")),
            "starters": {}, "teams": {}, "unavailable": {},
            "lineups": {},
            "source": "MLB Stats API", "source_url": "https://www.mlb.com/probable-pitchers",
            "updated_at": now.isoformat(timespec="seconds"),
        }
        raw_lineups = game.get("lineups") or {}
        for side in ("home", "away"):
            players = raw_lineups.get(f"{side}Players") or []
            if players:
                rec["lineups"][side] = [{
                    "name": p.get("fullName"),
                    "position": (p.get("primaryPosition") or {}).get("abbreviation"),
                    "order": i + 1,
                } for i, p in enumerate(players) if p.get("fullName")]
        for side in ("home", "away"):
            side_row, team = teams[side], teams[side]["team"]
            pid = int((side_row.get("probablePitcher") or {}).get("id") or 0)
            if pid:
                rec["starters"][side] = {
                    "name": side_row["probablePitcher"].get("fullName"),
                    "player_id": pid, "announced": True, "stats": stats.get(pid),
                    "stats_source": "MLB Stats API 시즌 투구 기록",
                }
            league_record = side_row.get("leagueRecord") or {}
            rec["teams"][side] = {
                "wins": league_record.get("wins"), "losses": league_record.get("losses"),
                "pct": _num(league_record.get("pct")), "abbreviation": team.get("abbreviation"),
            }
            rec["unavailable"][side] = [
                {**x, "status": _injury_label(x.get("status"))}
                for x in injuries.get(str(team["id"]), [])]
        out.append(rec)
    return out, injuries, injury_stamp


def collect() -> dict:
    """야구·축구·농구·배구 선수 자료를 합치고, 외부 장애 때 직전 캐시를 유지한다."""
    try:
        existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    announcements = announcement_games()
    games = announcements
    npb_name_cache = existing.get("npb_name_cache") or {}
    try:
        npb = collect_npb_games(PICKS, _session(), name_cache=npb_name_cache)
    except (requests.RequestException, OSError, ValueError) as exc:
        print(f"NPB.jp 공식정보 오류 — 네이버/직전 캐시 유지: {type(exc).__name__}: {exc}", flush=True)
        npb = [g for g in existing.get("games", []) if g.get("league") == "NPB"]
    injuries = existing.get("team_injuries") or {}
    injury_stamp = existing.get("injuries_refreshed_at")
    try:
        mlb, injuries, injury_stamp = mlb_games(existing)
    except requests.RequestException as exc:
        print(f"MLB Stats API 오류 — 직전 캐시 유지: {type(exc).__name__}: {exc}", flush=True)
        mlb = [g for g in existing.get("games", []) if g.get("league") == "MLB"]
    apply_korean_starter_names(mlb, announcements)
    soccer_cache = existing.get("soccer_team_cache") or {}
    try:
        soccer, soccer_cache = collect_soccer_info(existing, PICKS, _session())
    except (requests.RequestException, OSError, ValueError) as exc:
        print(f"축구 선수정보 오류 — 직전 캐시 유지: {type(exc).__name__}: {exc}", flush=True)
        soccer = [g for g in existing.get("games", []) if g.get("league") in SOCCER_CATS]
    court_cache = existing.get("court_team_cache") or {}
    try:
        court, court_cache = collect_court_info(existing, PICKS, _session())
    except (requests.RequestException, OSError, ValueError) as exc:
        print(f"농구·배구 선수정보 오류 — 직전 캐시 유지: {type(exc).__name__}: {exc}", flush=True)
        court = [g for g in existing.get("games", []) if g.get("sport") in COURT_SPORTS]
    # 공식 API 범위 밖의 과거 MLB 경기는 네이버 예고 이름을 유지하고, 공식 경기에는
    # 네이버 한글 선발명과 MLB 기록을 합친다. 최신 공식 레코드가 화면에 사용된다.
    games = games + npb + mlb + soccer + court
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "injuries_refreshed_at": injury_stamp, "team_injuries": injuries,
        "npb_name_cache": npb_name_cache, "soccer_team_cache": soccer_cache,
        "court_team_cache": court_cache, "games": games,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    return doc


def _mmdd(value: str) -> str | None:
    text = str(value or "")
    # ISO `2026-08-23`에서 느슨한 정규식은 `26-08`을 월·일로 잘못 잡는다.
    iso = re.match(r"\d{4}-(\d{2})-(\d{2})", text)
    if iso:
        return f"{iso.group(1)}.{iso.group(2)}"
    m = re.search(r"(?:^|\D)(\d{2})[.\-/](\d{2})(?:\D|$)", text)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _kickoff_minute(value: str, reference: datetime | None = None) -> str | None:
    """ISO 또는 프로토 MM.DD HH:MM을 KST 연월일·분 키로 정규화한다."""
    text = str(value or "").strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp.astimezone(KST)
            return stamp.isoformat(timespec="minutes")
    except ValueError:
        return None
    match = re.search(r"(\d{2})\.(\d{2}).*?(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    month, day, hour, minute = map(int, match.groups())
    anchor = reference or datetime.now(KST)
    anchor = anchor.replace(tzinfo=KST) if anchor.tzinfo is None else anchor.astimezone(KST)
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=KST))
        except ValueError:
            continue
    if not candidates:
        return None
    stamp = min(candidates, key=lambda candidate: abs((candidate - anchor).total_seconds()))
    return stamp.isoformat(timespec="minutes")


def game_index(doc: dict | None = None) -> dict[tuple, list[dict]]:
    if doc is None:
        try:
            doc = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {"games": announcement_games()}
    mapping, out = _team_map(), defaultdict(list)
    for rec in doc.get("games", []):
        key = (str(rec.get("league")), _kickoff_minute(rec.get("game_datetime")),
               canonical_team(rec.get("league", ""), rec.get("home_team", ""), mapping),
               canonical_team(rec.get("league", ""), rec.get("away_team", ""), mapping))
        out[key].append(rec)
    return dict(out)


def match_game(index: dict, league: str, game_date: str, home: str, away: str,
               reference: datetime | None = None) -> dict | None:
    mapping = _team_map()
    key = (league, _kickoff_minute(game_date, reference),
           canonical_team(league, home, mapping), canonical_team(league, away, mapping))
    candidates = index.get(key) or []
    if not candidates:
        return None
    # 동일 경기의 여러 자료원만 최신 관측으로 합친다. 시각이 다른 더블헤더는 키부터 다르다.
    rec = max(candidates, key=lambda x: str(x.get("updated_at") or ""))
    starters = rec.get("starters") or {}
    payload = {
        "home": (starters.get("home") or {}).get("name"),
        "away": (starters.get("away") or {}).get("name"),
        "home_detail": starters.get("home"), "away_detail": starters.get("away"),
        "teams": rec.get("teams") or {}, "unavailable": rec.get("unavailable") or {},
        "lineups": rec.get("lineups") or {}, "source": rec.get("source"),
        "source_url": rec.get("source_url"), "updated_at": rec.get("updated_at"),
        "key_players": rec.get("key_players") or {}, "rosters": rec.get("rosters") or {},
        "benches": rec.get("benches") or {}, "formations": rec.get("formations") or {},
        "lineup_status": rec.get("lineup_status") or {},
        "roster_status": rec.get("roster_status") or {},
        "coverage": rec.get("coverage") or {}, "sport": rec.get("sport"),
        "game_id": rec.get("game_id"), "game_datetime": rec.get("game_datetime"),
    }
    return enrich_availability(payload)


def enrich_picks(player_doc: dict, picks_path: Path = PICKS) -> int:
    """전체 예측을 다시 돌리지 않고 선수정보와 선수 해설만 안전하게 갱신한다.

    games.csv가 필요한 무거운 생성기는 매시간이지만 선발 변경은 경기 직전에도 난다.
    이 단계는 확률·추천·배당과 기본 해설을 건드리지 않는다.
    """
    if not picks_path.exists():
        return 0
    try:
        picks = json.loads(picks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    index, changed = game_index(player_doc), 0
    try:
        reference = datetime.fromisoformat(
            str(player_doc.get("generated_at") or "").replace("Z", "+00:00"))
    except ValueError:
        reference = datetime.now(KST)
    # 종료 경기에 이후 등판까지 포함한 최신 시즌 통계를 다시 붙이지 않는다.
    for bucket in ("live",):
        for game in picks.get(bucket, []):
            if game.get("sport") not in ({"bs", "sc"} | COURT_SPORTS):
                continue
            info = match_game(index, str(game.get("league")), str(game.get("date")),
                              str(game.get("home")), str(game.get("away")), reference)
            game_changed = False
            if game.get("선발") != info:
                game["선발"] = info
                game_changed = True
            base = game.get("해설기본")
            if not base and game.get("해설"):
                base = game["해설"]
                game["해설기본"] = base
                game_changed = True
            refreshed = with_player_context(
                base, str(game.get("home")), str(game.get("away")),
                str(game.get("sport")), info)
            if base and game.get("해설") != refreshed:
                game["해설"] = refreshed
                game_changed = True
            if game_changed:
                changed += 1
    if not changed and picks.get("player_info_at") == player_doc.get("generated_at"):
        return 0
    picks["player_info_at"] = player_doc.get("generated_at")
    tmp = picks_path.with_suffix(".player.tmp")
    tmp.write_text(json.dumps(picks, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(picks_path)
    return changed


def _selftest() -> int:
    soccer_selftest()
    court_selftest()
    mapping = {"MLB": {"뉴욕양키": "뉴욕양키스", "토론블루": "토론토"}}
    assert canonical_team("MLB", "뉴욕양키", mapping) == "뉴욕양키스"
    assert canonical_team("MLB", "뉴욕 양키스", mapping) == "뉴욕양키스"
    assert abs(baseball_innings("5 ⅔") - 5 - 2 / 3) < 1e-9
    assert abs(baseball_innings("103.1") - 103 - 1 / 3) < 1e-9
    assert _mmdd("2026-08-23T02:35:00+09:00") == "08.23"
    official = [{
        "league": "MLB", "game_datetime": "2026-08-23T02:35:00+09:00",
        "home_team": "뉴욕양키스", "away_team": "토론토",
        "starters": {"home": {"name": "Garrett Cole", "stats": {"era": 2.9}}},
    }]
    announced = [{
        "league": "MLB", "game_datetime": "2026-08-23T02:35:00",
        "home_team": "뉴욕양키스", "away_team": "토론토",
        "updated_at": "2026-08-22T12:00:00+00:00",
        "starters": {"home": {"name": "게릿 콜"}},
    }]
    apply_korean_starter_names(official, announced)
    assert official[0]["starters"]["home"] == {
        "name": "게릿 콜", "native_name": "Garrett Cole", "stats": {"era": 2.9},
    }

    sample = {("MLB", "2026-08-23T02:35+09:00", "뉴욕양키스", "토론토"): [{
        "league": "MLB", "updated_at": "2026-08-23T00:00:00+00:00",
        "starters": {"home": {"name": "A", "stats": {"era": 3.2}}, "away": {"name": "B"}},
    }]}
    got = match_game(sample, "MLB", "08.23(일) 02:35", "뉴욕양키", "토론블루",
                     datetime(2026, 8, 23, tzinfo=KST))
    assert got and got["home"] == "A" and got["home_detail"]["stats"]["era"] == 3.2
    with TemporaryDirectory() as td:
        path = Path(td) / "picks.json"
        path.write_text(json.dumps({"live": [{
            "sport": "bs", "league": "MLB", "date": "08.23(일) 02:35",
            "home": "뉴욕양키", "away": "토론블루", "추천": {"모델확률": .51},
            "선발": {"home": "어제 투수"},
        }, {
            "sport": "sc", "league": "EPL", "date": "08.23(일) 22:00",
            "home": "맨체스C", "away": "본머스", "추천": {"모델확률": .58},
        }, {
            "sport": "bk", "league": "남농월예", "date": "08.28(금) 00:30",
            "home": "핀란드M", "away": "스웨덴M", "추천": {"모델확률": .61},
        }], "past": []}, ensure_ascii=False), encoding="utf-8")
        player_doc = {"generated_at": "2026-08-23T00:00:00+00:00", "games": [{
            "league": "MLB", "game_datetime": "2026-08-23T02:35:00+09:00",
            "home_team": "뉴욕양키스", "away_team": "토론토",
            "updated_at": "2026-08-23T00:00:00+00:00",
            "starters": {"home": {"name": "오늘 투수", "stats": {"era": 3.2}}},
            "source": "MLB Stats API",
        }, {
            "league": "EPL", "game_datetime": "2026-08-23T22:00:00+09:00",
            "home_team": "맨체스C", "away_team": "본머스",
            "updated_at": "2026-08-23T00:00:00+00:00", "starters": {},
            "key_players": {"home": [{"name": "A", "goals": 2}]},
            "source": "네이버 스포츠 공식 경기·시즌 기록",
        }, {
            "sport": "bk", "league": "남농월예", "game_datetime": "2026-08-28T00:30:00+09:00",
            "home_team": "핀란드M", "away_team": "스웨덴M",
            "updated_at": "2026-08-23T00:00:00+00:00", "starters": {},
            "rosters": {"home": [{"name": "C", "position": "PG"}]},
            "roster_status": {"state": "official_competition_roster"},
            "source": "FIBA 공식 경기·대회 기록",
        }]}
        assert enrich_picks(player_doc, path) == 3
        enriched = json.loads(path.read_text(encoding="utf-8"))
        assert enriched["live"][0]["선발"]["home"] == "오늘 투수"
        assert enriched["live"][1]["선발"]["key_players"]["home"][0]["goals"] == 2
        assert enriched["live"][2]["선발"]["rosters"]["home"][0]["position"] == "PG"
        assert enriched["live"][2]["선발"]["roster_status"]["state"] == "official_competition_roster"
        # 30분 경량 갱신은 예측·추천 값을 절대 바꾸지 않는다.
        assert enriched["live"][0]["추천"]["모델확률"] == .51
        assert enriched["live"][1]["추천"]["모델확률"] == .58
        assert enriched["live"][2]["추천"]["모델확률"] == .61
        assert enriched["player_info_at"] == player_doc["generated_at"]
    print("[OK] 선수정보 - 팀 별칭, 날짜 경기키, 이닝 변환, 상세 선발 결합")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return _selftest()
    delay = None
    if "--loop" in argv:
        i = argv.index("--loop")
        delay = int(argv[i + 1]) if i + 1 < len(argv) else 1800
    while True:
        try:
            doc = collect()
            enriched = enrich_picks(doc)
            both = sum(1 for g in doc["games"] if len(g.get("starters") or {}) == 2)
            soccer = sum(1 for g in doc["games"] if g.get("league") in SOCCER_CATS)
            print(f"선수정보 {len(doc['games'])}경기 · 야구 양쪽 선발 {both} · "
                  f"축구 {soccer}경기 · 화면 {enriched}경기 갱신 · 저장 {OUT}", flush=True)
        except Exception as exc:  # noqa: BLE001 — 상시 수집기는 다음 주기에 복구해야 한다
            print(f"선수정보 수집 오류: {type(exc).__name__}: {exc}", flush=True)
            if delay is None:
                return 1
        if delay is None:
            return 0
        time.sleep(max(delay, 60))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
