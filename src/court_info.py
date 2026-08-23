"""프로토 농구·배구 경기의 공식 선수·팀 자료를 결합한다.

농구 국가대표 경기는 FIBA 공개 경기 API, NBA·KBL·WKBL·V리그는 네이버
스포츠, 배구 국가대표는 Volleyball World의 최근 국제대회 명단을 사용한다.
등록 명단을 당일 선발 명단으로 오인하지 않도록 ``roster_status``를 별도로 둔다.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from soccer_info import _naver_start, _proto_start, team_similarity

KST = ZoneInfo("Asia/Seoul")
TTL = timedelta(hours=6)
FIBA_API = "https://digital-api.fiba.basketball/hapi"
# FIBA 웹 클라이언트에 공개되어 있는 구독 키다. 교체 시 환경변수가 우선한다.
FIBA_KEY = os.environ.get(
    "FIBA_APIM_SUBSCRIPTION_KEY", "898cd5e738914002" "8ecb42943c47eb74")
NAVER_API = "https://api-gw.sports.naver.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
}

COURT_SPORTS = {"bk", "vl"}
NAVER_CATS = {
    ("bk", "NBA"): ("basketball", "nba"),
    ("bk", "NBA컵"): ("basketball", "nba"),
    ("bk", "KBL"): ("basketball", "kbl"),
    ("bk", "KBL컵"): ("basketball", "kbl"),
    ("bk", "WKBL"): ("basketball", "wkbl"),
    ("bk", "박신자컵"): ("basketball", "wkbl"),
    ("vl", "KOVO남"): ("volleyball", "kovo"),
    ("vl", "KOVO컵남"): ("volleyball", "kovo"),
    ("vl", "KOVO여"): ("volleyball", "wkovo"),
    ("vl", "KOVO컵여"): ("volleyball", "wkovo"),
}

# 현재 프로토 국가대표 축약명 → FIBA/Volleyball World 국가 코드.
COUNTRY_CODES = {
    "핀란드": "FIN", "스웨덴": "SWE", "카타르": "QAT", "중국": "CHN",
    "헝가리": "HUN", "에스토": "EST", "사우디": "KSA", "일본": "JPN",
    "독일": "GER", "네덜란": "NED", "레바논": "LBN", "한국": "KOR",
    "이스라": "ISR", "폴란드": "POL", "크로아": "CRO", "라트비": "LAT",
    "프랑스": "FRA", "슬로베": "SLO", "아르헨": "ARG", "푸에르": "PUR",
    "브라질": "BRA", "도미공": "DOM", "우루과": "URU", "바하마": "BAH",
    "칠레": "CHI", "미국": "USA", "파나마": "PAN", "캐나다": "CAN",
    "멕시코": "MEX", "콜롬비": "COL", "이란": "IRI", "뉴질랜": "NZL",
    "시리아": "SYR", "호주": "AUS", "요르단": "JOR", "필리핀": "PHI",
    "홍콩": "HKG",
}

VOLLEY_ROSTERS = {
    "JPN": "https://en.volleyballworld.com/volleyball/competitions/volleyball-nations-league/teams/women/7545/players/",
    "KOR": "https://en.volleyballworld.com/volleyball/competitions/volleyball-nations-league/teams/women/7546/players/",
}
VOLLEY_STATS = "https://en.volleyballworld.com/volleyball/competitions/volleyball-nations-league/statistics/women/best-scorers/"


def _num(value, digits: int | None = None):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if digits is not None:
        number = round(number, digits)
    return int(number) if number.is_integer() else number


def _team_code(name: str) -> str | None:
    key = re.sub(r"[^가-힣A-Za-z]", "", str(name or "")).rstrip("MWmw")
    return COUNTRY_CODES.get(key)


def _fresh(row: dict, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        return now - stamp.astimezone(KST) < TTL
    except (TypeError, ValueError):
        return False


def _load_games(picks_path: Path, now: datetime) -> list[dict]:
    try:
        data = json.loads(picks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for game in data.get("live", []):
        if game.get("sport") not in COURT_SPORTS:
            continue
        start = _proto_start(str(game.get("date") or ""), now)
        if start:
            out.append({**game, "_start": start})
    return out


def _fiba_get(path: str):
    response = requests.get(
        f"{FIBA_API}/{path}", headers={**HEADERS, "Ocp-Apim-Subscription-Key": FIBA_KEY}, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def _fiba_start(row: dict) -> datetime | None:
    text = str(row.get("gameDateTimeUTC") or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST)
    except ValueError:
        return None


def _fiba_schedule(games: list[dict]) -> list[dict]:
    basketball = [g for g in games if g["sport"] == "bk" and _team_code(g.get("home")) and _team_code(g.get("away"))]
    if not basketball:
        return []
    lo = min(g["_start"] for g in basketball).date() - timedelta(days=1)
    hi = max(g["_start"] for g in basketball).date() + timedelta(days=1)
    rows = _fiba_get(f"getgdapgamesbetweentwodates?dateFrom={lo.isoformat()}&dateTo={hi.isoformat()}")
    return rows if isinstance(rows, list) else []


def _match_fiba(game: dict, rows: list[dict]) -> dict | None:
    home, away = _team_code(game.get("home")), _team_code(game.get("away"))
    exact = []
    for row in rows:
        if (row.get("teamA") or {}).get("code") != home or (row.get("teamB") or {}).get("code") != away:
            continue
        start = _fiba_start(row)
        if start and abs((start - game["_start"]).total_seconds()) <= 90 * 60:
            exact.append((abs((start - game["_start"]).total_seconds()), row))
    return min(exact, key=lambda x: x[0])[1] if exact else None


def _fiba_team(team_id: str, cache: dict, now: datetime) -> dict:
    key = f"fiba:{team_id}"
    if _fresh(cache.get(key) or {}, now):
        return cache[key]
    stats = _fiba_get(f"getgdapcompetitionteamstatisticsbyteamid?gdapTeamId={team_id}") or {}
    roster = _fiba_get(f"getgdapcompetitionteamlatestrosterbyteamid?gdapTeamId={team_id}") or {}
    positions = {
        str(p.get("personId")): p for p in roster.get("players", []) if p.get("personId")
    }
    players = []
    for row in stats.get("playerInCompetitionTeamStatistics", []):
        if not row.get("totalGamesPlayed"):
            continue
        profile = positions.get(str(row.get("playerId"))) or {}
        players.append({
            "player_id": str(row.get("playerId") or ""),
            "name": " ".join(x for x in (row.get("firstName"), row.get("lastName")) if x),
            "position": profile.get("position"), "games": _num(row.get("totalGamesPlayed")),
            "points": _num(row.get("pointsPerGame"), 1),
            "rebounds": _num(row.get("reboundsPerGame"), 1),
            "assists": _num(row.get("assistsPerGame"), 1),
            "efficiency": _num(row.get("efficiencyPerGame"), 1),
            "fg_pct": _num(row.get("fieldGoalsPercentage"), 1),
            "three_pct": _num(row.get("threePointsPercentage"), 1),
            "minutes": _num((_num(row.get("playTimeInSecondsPerGame")) or 0) / 60, 1),
        })
    players.sort(key=lambda p: (p.get("efficiency") or -999, p.get("points") or -999), reverse=True)
    registered = []
    for p in roster.get("players", []):
        if not p.get("isOnFinalRoster"):
            continue
        registered.append({
            "player_id": str(p.get("personId") or ""),
            "name": " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x),
            "position": p.get("position"), "number": p.get("uniformNumber"),
            "club": p.get("clubName"), "height": _num(p.get("heightInCm")),
        })
    rec = {
        "updated_at": now.isoformat(timespec="seconds"), "players": players[:5],
        "roster": registered, "roster_code": roster.get("finalRosterStatusCode"),
        "team": {
            "wins": _num(stats.get("totalGamesWon")), "losses": _num(stats.get("totalGamesLost")),
            "games": _num(stats.get("totalGamesPlayed")), "points_per_game": _num(stats.get("pointsPerGame"), 1),
            "rebounds_per_game": _num(stats.get("reboundsPerGame"), 1),
            "assists_per_game": _num(stats.get("assistsPerGame"), 1),
            "turnovers_per_game": _num(stats.get("turnoversPerGame"), 1),
            "efficiency_per_game": _num(stats.get("efficiencyPerGame"), 1),
            "fg_pct": _num(stats.get("fieldGoalsPercentage"), 1),
            "three_pct": _num(stats.get("threePointsPercentage"), 1),
            "ft_pct": _num(stats.get("freeThrowsPercentage"), 1),
        },
    }
    cache[key] = rec
    return rec


def _fiba_games(proto: list[dict], cache: dict, now: datetime) -> list[dict]:
    try:
        schedule = _fiba_schedule(proto)
    except requests.RequestException:
        return []
    matches = [(g, raw) for g in proto if g["sport"] == "bk" if (raw := _match_fiba(g, schedule))]
    team_ids = {str(raw[side]["teamId"]) for _, raw in matches for side in ("teamA", "teamB") if (raw.get(side) or {}).get("teamId")}
    details = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(team_ids)))) as pool:
        jobs = {pool.submit(_fiba_team, tid, cache, now): tid for tid in team_ids}
        for job in as_completed(jobs):
            try:
                details[jobs[job]] = job.result()
            except requests.RequestException:
                pass
    out = []
    for game, raw in matches:
        side_data = {}
        for side, fiba_side in (("home", "teamA"), ("away", "teamB")):
            side_data[side] = details.get(str((raw.get(fiba_side) or {}).get("teamId"))) or {}
        if not any(side_data.values()):
            continue
        roster_sides = [bool(side_data[s].get("roster")) for s in ("home", "away")]
        if all(roster_sides):
            roster_status = {"state": "official_competition_roster",
                             "label": "FIBA 공식 대회 등록 명단 · 당일 출전 명단과 다를 수 있음"}
            coverage = {"state": "official", "label": "일정·양 팀 대회명단·선수/팀 통계 연결"}
        elif any(roster_sides):
            roster_status = {"state": "official_roster_partial",
                             "label": "FIBA 등록 명단 일부 공개 · 미공개 팀은 대회 선수 기록만 제공"}
            coverage = {"state": "partial", "label": "일정·선수/팀 통계 연결 · 한쪽 최종 명단 미공개"}
        else:
            roster_status = {"state": "official_player_stats",
                             "label": "FIBA 대회 선수 기록 · 양 팀 최종 명단은 아직 미공개"}
            coverage = {"state": "partial", "label": "일정·선수/팀 통계 연결 · 최종 명단 미공개"}
        out.append({
            "sport": "bk", "league": game["league"], "game_id": str(raw.get("gameId") or ""),
            "game_datetime": game["_start"].isoformat(), "home_team": game.get("home"), "away_team": game.get("away"),
            "starters": {}, "key_players": {s: side_data[s].get("players", []) for s in ("home", "away")},
            "rosters": {s: side_data[s].get("roster", []) for s in ("home", "away")},
            "teams": {s: side_data[s].get("team", {}) for s in ("home", "away")},
            "unavailable": {}, "roster_status": roster_status,
            "coverage": coverage,
            "source": "FIBA 공식 경기·대회 기록", "source_url": f"https://www.fiba.basketball/en/games/{raw.get('gameId')}",
            "updated_at": now.isoformat(timespec="seconds"),
        })
    return out


def _parse_volley_roster(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    players = []
    for row in soup.select("table.vbw-team-roster-table tbody tr"):
        name = row.select_one("td.playername")
        pos = row.select_one("td.position")
        num = row.select_one("td.shirtnumber")
        link = name.select_one("a") if name else None
        if not name or not name.get_text(strip=True):
            continue
        href = link.get("href", "") if link else ""
        player_id = href.rstrip("/").split("/")[-1] if "/players/" in href else ""
        players.append({"player_id": player_id, "name": name.get_text(" ", strip=True),
                        "position": pos.get_text(" ", strip=True) if pos else None,
                        "number": num.get_text(" ", strip=True) if num else None})
    return players


def _parse_volley_stats(html: str) -> dict[str, list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, list[dict]] = {}
    for row in soup.select("tr.vbw-o-table__row--scorer"):
        def cell(cls):
            node = row.select_one(f"td.{cls}")
            return node.get_text(" ", strip=True) if node else None
        code, name = cell("federation"), cell("playername")
        if not code or not name:
            continue
        link = row.select_one("td.playername a")
        href = link.get("href", "") if link else ""
        out.setdefault(code, []).append({
            "player_id": href.rstrip("/").split("/")[-1] if "/players/" in href else "",
            "name": name, "rank": _num(cell("rank")), "points": _num(cell("points")),
            "attacks": _num(cell("attacks")), "blocks": _num(cell("blocks")), "serves": _num(cell("serves")),
        })
    return out


def _volleyball_games(proto: list[dict], cache: dict, session: requests.Session, now: datetime) -> list[dict]:
    games = [g for g in proto if g["sport"] == "vl"]
    if not games:
        return []
    stat_cache = cache.get("volley:stats") or {}
    if not _fresh(stat_cache, now):
        try:
            response = session.get(VOLLEY_STATS, headers=HEADERS, timeout=30)
            response.raise_for_status()
            stat_cache = {"updated_at": now.isoformat(timespec="seconds"), "teams": _parse_volley_stats(response.text)}
            cache["volley:stats"] = stat_cache
        except requests.RequestException:
            pass
    rosters = {}
    for game in games:
        for name in (game.get("home"), game.get("away")):
            code = _team_code(name)
            url = VOLLEY_ROSTERS.get(code or "")
            if not code or not url or code in rosters:
                continue
            key = f"volley:roster:{code}"
            saved = cache.get(key) or {}
            if _fresh(saved, now):
                rosters[code] = saved
                continue
            try:
                response = session.get(url, headers=HEADERS, timeout=30)
                response.raise_for_status()
                saved = {"updated_at": now.isoformat(timespec="seconds"), "url": url,
                         "players": _parse_volley_roster(response.text)}
                cache[key] = saved
                rosters[code] = saved
            except requests.RequestException:
                pass
    stats_by_code = stat_cache.get("teams") or {}
    out = []
    for game in games:
        codes = {"home": _team_code(game.get("home")), "away": _team_code(game.get("away"))}
        if not any(code in rosters for code in codes.values() if code):
            continue
        roster_payload, key_payload = {}, {}
        for side, code in codes.items():
            roster_payload[side] = (rosters.get(code or "") or {}).get("players", [])
            positions = {p.get("player_id"): p.get("position") for p in roster_payload[side]}
            key_payload[side] = []
            for player in (stats_by_code.get(code or "") or [])[:5]:
                key_payload[side].append({**player, "position": positions.get(player.get("player_id"))})
        source_url = next(((rosters.get(c or "") or {}).get("url") for c in codes.values() if c in rosters), VOLLEY_STATS)
        out.append({
            "sport": "vl", "league": game["league"], "game_datetime": game["_start"].isoformat(),
            "home_team": game.get("home"), "away_team": game.get("away"), "starters": {},
            "key_players": key_payload, "rosters": roster_payload, "teams": {}, "unavailable": {},
            "roster_status": {"state": "recent_international_roster",
                              "label": "Volleyball World 최신 국제대회 명단 · 이번 대회 확정 명단 아님"},
            "coverage": {"state": "partial", "label": "최근 대표팀 명단·VNL 선수 득점 연결"},
            "source": "Volleyball World 공식 선수·대회 기록", "source_url": source_url,
            "updated_at": now.isoformat(timespec="seconds"),
        })
    return out


def _naver_record(row: dict, sport: str) -> dict:
    common = {"rank": _num(row.get("rank")), "wins": _num(row.get("wins")),
              "losses": _num(row.get("losses")), "games": _num(row.get("matchesPlayed"))}
    if sport == "bk":
        return {**common, "pct": _num(row.get("winRate"), 3),
                "points_per_game": _num(row.get("pointsPerGame"), 1),
                "conceded_per_game": _num(row.get("pointsConcededPerGame"), 1),
                "rebounds_per_game": _num(row.get("reboundPerGame"), 1),
                "assists_per_game": _num(row.get("assistPerGame"), 1),
                "fg_pct": _num(row.get("fieldGoalThrowSuccessRate"), 1),
                "three_pct": _num(row.get("threePointSuccessRate"), 1),
                "turnovers_per_game": _num(row.get("turnoverPerGame"), 1)}
    return {**common, "table_points": _num(row.get("points")), "set_ratio": _num(row.get("setRatio"), 2),
            "point_ratio": _num(row.get("pointRatio"), 2), "attack_pct": _num(row.get("hittingSuccessRate"), 1),
            "blocks_per_set": _num(row.get("blockingPerSet"), 2), "serves_per_set": _num(row.get("servesPerSet"), 2),
            "digs_per_set": _num(row.get("digPerSet"), 2), "receive_efficiency": _num(row.get("receiveEfficiency"), 1)}


def _naver_player(row: dict, sport: str) -> dict:
    if sport == "vl":
        return {"player_id": str(row.get("playerId") or ""), "name": row.get("playerName"),
                "position": row.get("position"), "games": _num(row.get("matchesPlayed")),
                "points": _num(row.get("point")), "attacks": _num(row.get("hittingSuccess")),
                "blocks": _num(row.get("blockingSuccess")), "serves": _num(row.get("servesSuccess")),
                "attack_pct": _num((_num(row.get("hittingSuccess")) or 0) / max(1, _num(row.get("hittingTotal")) or 0) * 100, 1)}
    return {"player_id": str(row.get("playerId") or ""), "name": row.get("playerName") or row.get("fullName"),
            "position": row.get("position"), "games": _num(row.get("matchesPlayed")),
            "points": _num(row.get("pointsPerGame"), 1), "rebounds": _num(row.get("reboundPerGame"), 1),
            "assists": _num(row.get("assistPerGame"), 1), "efficiency": _num(row.get("efficiency"), 1)}


def _naver_games(proto: list[dict], session: requests.Session, now: datetime) -> list[dict]:
    candidates = [g for g in proto if (g["sport"], g["league"]) in NAVER_CATS]
    out = []
    for game in candidates:
        super_cat, cat = NAVER_CATS[(game["sport"], game["league"])]
        params = {"fields": "all", "superCategoryId": super_cat, "categoryId": cat,
                  "fromDate": (game["_start"].date() - timedelta(days=1)).isoformat(),
                  "toDate": (game["_start"].date() + timedelta(days=1)).isoformat(), "size": 100}
        response = session.get(f"{NAVER_API}/schedule/games", params=params,
                               headers={**HEADERS, "Referer": "https://m.sports.naver.com/"}, timeout=25)
        response.raise_for_status()
        rows = (response.json().get("result") or {}).get("games", [])
        ranked = []
        for raw in rows:
            start = _naver_start(raw.get("gameDateTime"))
            if not start or abs((start - game["_start"]).total_seconds()) > 30 * 60:
                continue
            score = team_similarity(game.get("home"), raw.get("homeTeamName")) + team_similarity(game.get("away"), raw.get("awayTeamName"))
            ranked.append((score, raw))
        if not ranked or max(ranked, key=lambda x: x[0])[0] < 1.05:
            continue
        raw = max(ranked, key=lambda x: x[0])[1]
        season = str(raw.get("seasonCode") or "")
        if not season:
            continue
        teams_response = session.get(f"{NAVER_API}/statistics/categories/{cat}/seasons/{season}/teams",
                                     headers={**HEADERS, "Referer": "https://m.sports.naver.com/"}, timeout=25)
        teams_response.raise_for_status()
        table = {(str(x.get("teamId"))): x for x in (teams_response.json().get("result") or {}).get("seasonTeamStats", [])}
        players_response = session.get(f"{NAVER_API}/statistics/categories/{cat}/seasons/{season}/players",
                                       params={"page": 1, "pageSize": 500},
                                       headers={**HEADERS, "Referer": "https://m.sports.naver.com/"}, timeout=25)
        players_response.raise_for_status()
        all_players = (players_response.json().get("result") or {}).get("seasonPlayerStats", [])
        key_players, teams = {}, {}
        for side in ("home", "away"):
            tid = str(raw.get(f"{side}TeamCode") or "")
            teams[side] = _naver_record(table.get(tid) or {}, game["sport"])
            rows_for_team = [_naver_player(p, game["sport"]) for p in all_players if str(p.get("teamId")) == tid and _num(p.get("matchesPlayed"))]
            rows_for_team.sort(key=lambda p: (p.get("points") or 0, p.get("efficiency") or 0), reverse=True)
            key_players[side] = rows_for_team[:5]
        out.append({
            "sport": game["sport"], "league": game["league"], "game_id": str(raw.get("gameId") or ""),
            "game_datetime": game["_start"].isoformat(), "home_team": game.get("home"), "away_team": game.get("away"),
            "starters": {}, "key_players": key_players, "rosters": {}, "teams": teams, "unavailable": {},
            "roster_status": {"state": "season_stats", "label": "현재 시즌 선수 기록 · 당일 출전 명단 아님"},
            "coverage": {"state": "official", "label": "일정·선수/팀 시즌 통계 연결"},
            "source": "네이버 스포츠 공식 경기·시즌 기록",
            "source_url": f"https://m.sports.naver.com/{super_cat}/schedule/index?category={cat}",
            "updated_at": now.isoformat(timespec="seconds"),
        })
    return out


def _fallback(game: dict, now: datetime) -> dict:
    return {"sport": game["sport"], "league": game["league"], "game_datetime": game["_start"].isoformat(),
            "home_team": game.get("home"), "away_team": game.get("away"), "starters": {}, "key_players": {},
            "rosters": {}, "teams": {}, "unavailable": {},
            "coverage": {"state": "unavailable", "label": "공식 선수·명단 자료원 미연결 · 팀 최근 흐름만 제공"},
            "updated_at": now.isoformat(timespec="seconds")}


def collect(existing: dict, picks_path: Path, session: requests.Session,
            now: datetime | None = None) -> tuple[list[dict], dict]:
    now = now or datetime.now(KST)
    proto = _load_games(picks_path, now)
    cache = dict(existing.get("court_team_cache") or {})
    collected = []
    try:
        collected += _fiba_games(proto, cache, now)
    except requests.RequestException:
        pass
    try:
        collected += _volleyball_games(proto, cache, session, now)
    except requests.RequestException:
        pass
    try:
        collected += _naver_games(proto, session, now)
    except requests.RequestException:
        pass
    keys = {(g["sport"], g["league"], g["game_datetime"], g["home_team"], g["away_team"]) for g in collected}
    for game in proto:
        key = (game["sport"], game["league"], game["_start"].isoformat(), game.get("home"), game.get("away"))
        if key not in keys:
            collected.append(_fallback(game, now))
    return collected, cache


def selftest() -> None:
    now = datetime(2026, 8, 23, 1, 0, tzinfo=KST)
    assert _team_code("핀란드M") == "FIN" and _team_code("한국W") == "KOR"
    raw = {"gameDateTimeUTC": "2026-08-27T15:30:00", "teamA": {"code": "FIN"}, "teamB": {"code": "SWE"}}
    game = {"home": "핀란드M", "away": "스웨덴M", "_start": datetime(2026, 8, 28, 0, 30, tzinfo=KST)}
    assert _match_fiba(game, [raw]) is raw
    roster = '<table class="vbw-team-roster-table"><tbody><tr><td class="shirtnumber">4</td><td class="playername"><a href="/players/1">A</a></td><td class="position">OH</td></tr></tbody></table>'
    assert _parse_volley_roster(roster)[0]["position"] == "OH"
