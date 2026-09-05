"""현재 프로토 축구 경기에 네이버의 선수·팀·실제 라인업을 결합한다.

공개 전 라인업을 예상 명단처럼 표시하지 않는다. 경기 전에는 공식 시즌 기록의
핵심 선수와 팀 기록만 보여주고, 킥오프 두 시간 전부터 실제 라인업 엔드포인트를
확인해 발표된 명단만 별도 필드에 싣는다.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from runtime_db import load_artifact

from japan_info import (JLEAGUE_STANDINGS_URL, collect_jleague_standings,
                        jleague_record_for)

NAVER_API = "https://api-gw.sports.naver.com"
KST = ZoneInfo("Asia/Seoul")
STATS_TTL = timedelta(hours=6)
NAVER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
    "Accept": "application/json",
    "Referer": "https://m.sports.naver.com/",
}

# 프로토 리그명 → 네이버 스포츠 카테고리.
SOCCER_CATS = {
    "K리그1": ("kfootball", "kleague"),
    "K리그2": ("kfootball", "kleague2"),
    "MLS": ("wfootball", "mls"),
    "EPL": ("wfootball", "epl"),
    "라리가": ("wfootball", "primera"),
    "세리에A": ("wfootball", "seria"),
    "분데스리": ("wfootball", "bundesliga"),
    "독슈퍼컵": ("wfootball", "bundesliga"),
    "프리그1": ("wfootball", "ligue1"),
    "에레디비": ("wfootball", "eredivisie"),
    "EFL챔": ("wfootball", "england2"),
    "UCL": ("wfootball", "champs"),
    "UEL": ("wfootball", "europa"),
    "J1리그": ("wfootball", "jleague"),
    "J2리그": ("wfootball", "jleague2"),
    "잉리그컵": ("wfootball", "carlingcup"),
}

# 현재 프로토 일정과 네이버 동일 경기로 직접 검증한 축약명만 둔다.
TEAM_ALIASES = {
    "맨체스c": "맨시티", "앙제sco": "앙제", "릴osc": "릴", "헤이렌베": "히렌빈",
    "cf몽레알": "몬트리얼", "내슈빌sc": "내쉬빌", "콜럼크루": "콜롬버스",
    "새너어스": "어스퀘이크", "미네유나": "미네소타", "뉴잉레벌": "뉴잉글랜드",
    "뉴욕시티": "nycfc", "애틀유나": "애틀란타", "스포캔자": "스포팅kc",
}


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


def _proto_start(value: str, now: datetime) -> datetime | None:
    m = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", str(value or ""))
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    candidates = []
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=KST))
        except ValueError:
            pass
    return min(candidates, key=lambda x: abs((x - now).total_seconds())) if candidates else None


def _naver_start(value: str) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)
    except ValueError:
        return None


def _team_key(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()
    for prefix in ("fc",):
        if name.startswith(prefix) and len(name) > len(prefix) + 1:
            name = name[len(prefix):]
    name = TEAM_ALIASES.get(name, name)
    for suffix in ("footballclub", "fc", "hd", "상무", "하나", "시티", "유나이티드"):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[:-len(suffix)]
            break
    return name


def team_similarity(left: str, right: str) -> float:
    """축약된 프로토 팀명과 네이버 정식 팀명을 보수적으로 비교."""
    a, b = _team_key(left), _team_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        return .96
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    aa = {a[i:i + 2] for i in range(max(1, len(a) - 1))}
    bb = {b[i:i + 2] for i in range(max(1, len(b) - 1))}
    jac = len(aa & bb) / len(aa | bb) if aa | bb else 0.0
    return max(seq, jac)


def _load_proto_games(picks_path: Path, now: datetime) -> list[dict]:
    picks = load_artifact("picks_v2", picks_path) or {}
    out = []
    for game in picks.get("live", []):
        if game.get("sport") != "sc" or game.get("league") not in SOCCER_CATS:
            continue
        start = _proto_start(game.get("date", ""), now)
        if start:
            out.append({**game, "_start": start})
    return out


def _get(session: requests.Session, path: str, params: dict | None = None) -> dict:
    response = session.get(f"{NAVER_API}{path}", params=params, headers=NAVER_HEADERS, timeout=25)
    response.raise_for_status()
    body = response.json()
    return body.get("result") or {}


def _schedules(session: requests.Session, proto: list[dict]) -> dict[str, list[dict]]:
    if not proto:
        return {}
    lo = min(g["_start"] for g in proto).date() - timedelta(days=1)
    hi = max(g["_start"] for g in proto).date() + timedelta(days=1)
    by_spec: dict[tuple[str, str], list[dict]] = {}
    out = {}
    for league in sorted({str(g["league"]) for g in proto}):
        up, category = SOCCER_CATS[league]
        spec = (up, category)
        if spec not in by_spec:
            try:
                result = _get(session, "/schedule/games", {
                    "fields": "all", "upperCategoryId": up, "categoryId": category,
                    "fromDate": lo.isoformat(), "toDate": hi.isoformat(), "size": 500,
                })
                by_spec[spec] = [g for g in result.get("games", []) if not g.get("cancel")]
            except requests.RequestException:
                by_spec[spec] = []
        out[league] = by_spec[spec]
    return out


def _match(proto: dict, games: list[dict]) -> dict | None:
    ranked = []
    for game in games:
        start = _naver_start(game.get("gameDateTime"))
        if not start or abs((start - proto["_start"]).total_seconds()) > 15 * 60:
            continue
        hs = team_similarity(proto.get("home"), game.get("homeTeamName"))
        aws = team_similarity(proto.get("away"), game.get("awayTeamName"))
        ranked.append((hs + aws, min(hs, aws), game))
    if not ranked:
        return None
    total, weakest, game = max(ranked, key=lambda x: (x[0], x[1]))
    # 같은 시간대 경기가 여럿이어도 양 팀 중 한쪽이라도 전혀 닮지 않으면 붙이지 않는다.
    return game if total >= 1.05 and weakest >= .38 else None


def _fresh(row: dict, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        return now - stamp.astimezone(KST) < STATS_TTL
    except (TypeError, ValueError):
        return False


def _player(row: dict) -> dict:
    return {
        "player_id": str(row.get("playerId") or ""),
        "name": row.get("playerName") or row.get("fullName") or row.get("shortName"),
        "position": row.get("position") or row.get("positionName"),
        "apps": _num(row.get("matchesPlayed") if "matchesPlayed" in row else row.get("entryQty")),
        "starts": _num(row.get("matchesPlayedStarts")),
        "goals": _num(row.get("goals") if "goals" in row else row.get("glQty")),
        "assists": _num(row.get("assists") if "assists" in row else row.get("asQty")),
        "xg": _num(row.get("expectedGoals"), 2),
        "xa": _num(row.get("expectedAssists"), 2),
        "shots": _num(row.get("shots") if "shots" in row else row.get("stQty")),
        "shots_on_target": _num(row.get("shotsOnTarget") if "shotsOnTarget" in row else row.get("stValidQty")),
        "key_passes": _num(row.get("keyPasses")),
        "minutes": _num(row.get("minsPlayed") if "minsPlayed" in row else row.get("totalWtime")),
        "image": row.get("image"),
    }


def _player_rank(row: dict) -> tuple:
    p = _player(row)
    return ((_num(p.get("goals")) or 0) * 3 + (_num(p.get("assists")) or 0) * 2,
            _num(p.get("starts")) or 0, _num(p.get("minutes")) or 0)


def _team_record(row: dict) -> dict | None:
    if not row:
        return None
    return {
        "rank": _num(row.get("rank")),
        "played": _num(row.get("matchesPlayed")),
        "wins": _num(row.get("wins")), "draws": _num(row.get("draws")),
        "losses": _num(row.get("losses")), "points": _num(row.get("points")),
        "goals_per_game": _num(row.get("goalsPerGame"), 2),
        "conceded_per_game": _num(row.get("goalsConcededPerGame"), 2),
        "xg": _num(row.get("expectedGoals"), 2),
        "xga": _num(row.get("expectedGoalsConceded"), 2),
        "possession": _num(row.get("possession"), 1),
        "last_five": row.get("lastFiveGames"),
    }


def _foreign_team(session: requests.Session, cache: dict, category: str, season: str,
                  team_id: str, team_table: dict, now: datetime) -> dict:
    key = f"{category}:{season}:{team_id}"
    old = cache.get(key) or {}
    if _fresh(old, now):
        team = old.get("team")
        if isinstance(team, dict) and not any(value is not None for value in team.values()):
            team = None
        fresh = {**old, "team": team, "players": [
            p for p in old.get("players", []) if (p.get("apps") or 0) > 0]}
        cache[key] = fresh
        return fresh
    result = _get(session, f"/statistics/categories/{category}/seasons/{season}/players", {
        "teamCode": team_id, "sortField": "goals", "sortDirection": "desc",
        "page": 1, "pageSize": 40,
    })
    rows = result.get("seasonPlayerStats") or []
    rows = sorted(rows, key=_player_rank, reverse=True)
    players = [p for p in (_player(row) for row in rows) if p.get("name") and (p.get("apps") or 0) > 0][:7]
    rec = {
        "updated_at": now.isoformat(timespec="seconds"), "players": players,
        "team": _team_record(team_table.get(str(team_id), {})),
    }
    cache[key] = rec
    return rec


def _preview_team(preview: dict, side: str) -> tuple[list[dict], dict]:
    players = []
    for row in preview.get(f"{side}_team_best5_players") or []:
        players.append({
            "player_id": str(row.get("playerId") or ""), "name": row.get("playerName"),
            "position": None, "apps": _num(row.get("plays")),
            "starts": None, "goals": _num(row.get("goals")),
            "assists": _num(row.get("assists")), "xg": None, "xa": None,
        })
    raw = preview.get(f"{side}team_record") or {}
    if not raw:
        return players, None
    team = {
        "rank": _num(raw.get("rank")), "played": None,
        "wins": _num(raw.get("won")), "draws": _num(raw.get("drawn")),
        "losses": _num(raw.get("lost")), "points": None,
        "goals_per_game": _num(raw.get("gainGoalAvg"), 2),
        "conceded_per_game": _num(raw.get("lossGoalAvg"), 2),
    }
    return players, team


def _flatten_players(raw, stats: dict[str, dict]) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("lineup") or []
    if not isinstance(raw, list):
        return []
    out = []
    for group in raw:
        items = group if isinstance(group, list) else [group]
        for row in items:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            pid = str(row.get("playerId") or "")
            player = {
                "player_id": pid, "name": row.get("name"),
                "position": row.get("pos"), "number": row.get("shirtNumber"),
                "order": _num(row.get("positionOrder")),
            }
            if pid in stats:
                player["stats"] = stats[pid]
            out.append(player)
    return sorted(out, key=lambda p: p.get("order") or 99)


def _actual_lineup(session: requests.Session, game: dict, key_players: dict,
                   now: datetime) -> tuple[dict, dict, dict, dict]:
    start = _naver_start(game.get("gameDateTime"))
    status = str(game.get("statusCode") or "")
    if not start or (status == "BEFORE" and not (now - timedelta(hours=8) <= start <= now + timedelta(hours=2))):
        return {}, {}, {}, {"state": "before", "expected_at": (start - timedelta(hours=1)).isoformat() if start else None}
    try:
        result = _get(session, f"/schedule/games/{game.get('gameId')}/lineup")
    except requests.RequestException:
        return {}, {}, {}, {"state": "unavailable", "expected_at": (start - timedelta(hours=1)).isoformat()}
    data = result.get("lineUpData") or {}
    raw_lineup = data.get("lineup") or {}
    substitutions = data.get("substitution") or {}
    lineups, benches, formations = {}, {}, {}
    for side in ("home", "away"):
        known = {str(p.get("player_id")): p for p in key_players.get(side, []) if p.get("player_id")}
        side_data = raw_lineup.get(side) or {}
        players = _flatten_players(side_data.get("players"), known)
        bench = _flatten_players(substitutions.get(side), known)
        if players:
            lineups[side] = players
        if bench:
            benches[side] = bench
        if side_data.get("formation"):
            formations[side] = str(side_data["formation"])
    state = "announced" if lineups else "before"
    return lineups, benches, formations, {"state": state, "expected_at": (start - timedelta(hours=1)).isoformat()}


def _availability(key_players: dict, lineups: dict, benches: dict) -> dict:
    out = {"home": [], "away": []}
    for side in ("home", "away"):
        if not lineups.get(side):
            continue
        starters = {str(p.get("player_id")) for p in lineups[side]}
        bench = {str(p.get("player_id")) for p in benches.get(side, [])}
        for p in key_players.get(side, []):
            pid = str(p.get("player_id") or "")
            if not pid or pid in starters:
                continue
            out[side].append({
                "name": p.get("name"), "position": p.get("position"),
                "status": "벤치 시작" if pid in bench else "경기 명단 미포함 · 사유 확인 필요",
                "availability_status": "bench" if pid in bench else "out",
                "reason_code": "unknown", "source_type": "official_lineup",
            })
    return out


def collect(existing: dict, picks_path: Path, session: requests.Session,
            now: datetime | None = None) -> tuple[list[dict], dict]:
    now = now or datetime.now(KST)
    proto = _load_proto_games(picks_path, now)
    schedules = _schedules(session, proto)
    japanese_leagues = {str(g["league"]) for g in proto
                        if g.get("league") in JLEAGUE_STANDINGS_URL}
    official_jleague = collect_jleague_standings(session, japanese_leagues)
    cache = dict(existing.get("soccer_team_cache") or {})
    matches, seen = [], set()
    for game in proto:
        raw = _match(game, schedules.get(str(game["league"]), []))
        if not raw:
            continue
        unique = (game["league"], game["_start"].isoformat(), game.get("home"), game.get("away"))
        if unique in seen:
            continue
        seen.add(unique)
        matches.append((game, raw))

    # 해외 리그 팀 성적은 리그·시즌당 한 번만 받는다.
    tables = {}
    for _game, raw in matches:
        category, season = str(raw.get("categoryId") or ""), str(raw.get("seasonCode") or "")
        if not category or not season or category in ("kleague", "kleague2"):
            continue
        key = (category, season)
        if key in tables:
            continue
        try:
            result = _get(session, f"/statistics/categories/{category}/seasons/{season}/teams")
            tables[key] = {str(x.get("teamId")): x for x in result.get("seasonTeamStats", [])}
        except requests.RequestException:
            tables[key] = {}

    out = []
    for proto_game, raw in matches:
        category, season = str(raw.get("categoryId") or ""), str(raw.get("seasonCode") or "")
        key_players, teams = {"home": [], "away": []}, {}
        if category in ("kleague", "kleague2"):
            try:
                preview = (_get(session, f"/schedule/games/{raw.get('gameId')}/preview").get("previewData") or {})
            except requests.RequestException:
                preview = {}
            for side in ("home", "away"):
                key_players[side], teams[side] = _preview_team(preview, side)
        elif category and season:
            table = tables.get((category, season), {})
            for side in ("home", "away"):
                team_id = str(raw.get(f"{side}TeamCode") or "")
                if not team_id:
                    continue
                try:
                    info = _foreign_team(session, cache, category, season, team_id, table, now)
                    key_players[side], teams[side] = info.get("players", []), info.get("team", {})
                except requests.RequestException:
                    pass

        official_table = official_jleague.get(str(proto_game["league"])) or {}
        official_used = False
        for side in ("home", "away"):
            official = jleague_record_for(official_table, proto_game.get(side))
            if not official:
                continue
            # 네이버 값이 있으면 보존하되, 비어 있던 순위/성적은 J리그 공식표로 채운다.
            current = teams.get(side) if isinstance(teams.get(side), dict) else {}
            teams[side] = {
                **official, **{k: v for k, v in current.items() if v is not None},
            }
            official_used = True

        lineups, benches, formations, lineup_status = _actual_lineup(session, raw, key_players, now)
        rec = {
            "league": proto_game["league"], "game_id": str(raw.get("gameId") or ""),
            "sport": "sc",
            "game_datetime": proto_game["_start"].isoformat(),
            # 매칭을 통과한 뒤 프로토 표기로 저장해야 game_index가 확정적으로 다시 찾는다.
            "home_team": proto_game.get("home"), "away_team": proto_game.get("away"),
            "starters": {}, "key_players": key_players, "teams": teams,
            "unavailable": _availability(key_players, lineups, benches),
            "lineups": lineups, "benches": benches, "formations": formations,
            "lineup_status": lineup_status,
            "source": ("J.LEAGUE.jp 공식 순위 · 네이버 스포츠 경기기록"
                       if official_used else "네이버 스포츠 공식 경기·시즌 기록"),
            "source_url": (JLEAGUE_STANDINGS_URL.get(str(proto_game["league"]))
                           if official_used else
                           f"https://m.sports.naver.com/{SOCCER_CATS[proto_game['league']][0]}/schedule/index?category={category}"),
            "updated_at": now.isoformat(timespec="seconds"),
        }
        out.append(rec)

    # 네이버 일정의 시즌 코드/팀 매칭이 비어도 일본 공식 순위는 버리지 않는다.
    # 프로토 경기키로 최소 레코드를 만들어 팀 순위가 화면에서 사라지지 않게 한다.
    covered = {(g["league"], g["_start"].isoformat(), g.get("home"), g.get("away"))
               for g, _raw in matches}
    for game in proto:
        key = (game["league"], game["_start"].isoformat(), game.get("home"), game.get("away"))
        table = official_jleague.get(str(game["league"])) or {}
        if key in covered or not table:
            continue
        teams = {}
        for side in ("home", "away"):
            record = jleague_record_for(table, game.get(side))
            if record:
                teams[side] = record
        if not teams:
            continue
        out.append({
            "league": game["league"], "game_id": None,
            "sport": "sc",
            "game_datetime": game["_start"].isoformat(),
            "home_team": game.get("home"), "away_team": game.get("away"),
            "starters": {}, "key_players": {"home": [], "away": []}, "teams": teams,
            "unavailable": {"home": [], "away": []}, "lineups": {}, "benches": {},
            "formations": {}, "lineup_status": {
                "state": "before",
                "expected_at": (game["_start"] - timedelta(hours=1)).isoformat(),
            },
            "source": "J.LEAGUE.jp 공식 순위",
            "source_url": JLEAGUE_STANDINGS_URL[str(game["league"])],
            "updated_at": now.isoformat(timespec="seconds"),
        })
    # 현재 판매팀과 무관해진 오래된 캐시는 버려 JSON이 끝없이 커지는 것을 막는다.
    live_keys = set()
    for _game, raw in matches:
        category, season = str(raw.get("categoryId") or ""), str(raw.get("seasonCode") or "")
        for side in ("home", "away"):
            live_keys.add(f"{category}:{season}:{raw.get(f'{side}TeamCode') or ''}")
    cache = {k: v for k, v in cache.items() if k in live_keys}
    return out, cache


def selftest() -> None:
    now = datetime(2026, 8, 23, 1, 0, tzinfo=KST)
    assert _proto_start("08.23(일) 19:30", now) == datetime(2026, 8, 23, 19, 30, tzinfo=KST)
    assert _naver_start("08/23/2026 19:30:00") == datetime(2026, 8, 23, 19, 30, tzinfo=KST)
    assert team_similarity("FC서울", "서울") > .9
    assert team_similarity("브렌트퍼", "브렌트포드") > .6
    sample = {"_start": datetime(2026, 8, 23, 19, 30, tzinfo=KST),
              "home": "대전하나", "away": "강원FC"}
    got = _match(sample, [{"gameDateTime": "08/23/2026 19:30:00",
                           "homeTeamName": "대전", "awayTeamName": "강원"}])
    assert got is not None
    foreign = {"lineup": [[{"playerId": "1", "name": "A", "pos": "FW", "positionOrder": "11"}]]}
    assert _flatten_players(foreign, {"1": {"goals": 3}})[0]["stats"]["goals"] == 3
