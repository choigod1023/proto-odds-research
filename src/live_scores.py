"""실시간 점수 — 사이트가 경기 진행 상황을 바로 보여주게 한다.

왜 필요했나
-----------
`picks_v2.json` 에 `score` 칸이 있긴 했는데, **프로토가 회차를 '정산'해야** 채워졌다.
경기가 끝나고도 한참 뒤다. 게다가 산출물 생성(PUBLISH)은 6시간 주기라
구조상 실시간이 될 수가 없었다.

그래서 **가볍고 빠른 별도 수집기**를 둔다. 무거운 PUBLISH 와 분리한 이유가 그거다.
이 스크립트는 CSV 를 읽지 않고 API 만 때리므로 메모리도 거의 안 쓴다.

무엇을 쓰나
-----------
NAMED 스포츠 API를 기본 원천으로 쓴다. 축구·야구·농구 등 프로토 주요 종목을
한 번에 내려주며, 종료·진행·취소 상태와 점수도 포함한다. 네이버 일정 API는
KBO·MLB·NPB·K리그의 보조 원천과 야구 타석 상황용으로 계속 사용한다. NAMED의
핵심 필드는 `teams.*.periodData`, `gameStatus`, `startDatetime` 이다.

팀명 맞추기
-----------
사이트(프로토 표기)와 네이버 표기가 다르다(`마이말린` vs `마이애미`).
`data/processed/team_map.json` 이 이미 그 대응을 갖고 있으므로 **양방향으로 펴서**
프론트가 어느 쪽 이름으로 찾아도 맞도록 키를 여러 개 넣어 준다.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from runtime_db import load_artifact, load_document, persist_artifact

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "live_scores.json"
TEAM_MAP = ROOT / "data" / "processed" / "team_map.json"

API = "https://api-gw.sports.naver.com/schedule/games"
POLLING_API = API + "/{game_id}/game-polling"
NAMED_API = "https://sports-api.named.com/v1.0/popular-games"
PICKS = ROOT / "docs" / "data" / "picks_v2.json"
KST = timezone(timedelta(hours=9))

# 프로토가 파는 것 중 네이버가 커버하는 리그. game_detail.CATS 와 같은 표기.
CATS = {
    "KBO": ("kbaseball", "kbo"),
    "MLB": ("wbaseball", "mlb"),
    "NPB": ("wbaseball", "npb"),
    "K리그": ("kfootball", "kleague"),
}

TERMINAL_STATUSES = {"RESULT", "END", "ENDED", "CANCEL", "CANCELED", "CANCELLED", "POSTPONED"}
RESULT_STATUSES = {"RESULT", "END", "ENDED"}
HISTORY_DAYS = 45
FETCH_WORKERS = 5
SCHEDULE_TIMEOUT = (3, 8)
SITUATION_TIMEOUT = (3, 6)
FETCH_BUDGET_SECONDS = 120  # leave time to persist before supervisor's 180s limit
SITUATION_LIMIT = 12

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

NAMED_SPORTS = {
    "soccer": ("sc", "축구"),
    "baseball": ("bs", "야구"),
    "basketball": ("bk", "농구"),
    "volleyball": ("vl", "배구"),
}
NAMED_STATUS = {
    "READY": "BEFORE",
    "IN_PROGRESS": "STARTED",
    "BREAK_TIME": "STARTED",
    "FINAL": "RESULT",
    "CANCEL": "CANCEL",
    "CANCELED": "CANCEL",
    "CANCELLED": "CANCEL",
    "CUT": "CANCEL",
    "POSTPONED": "POSTPONED",
}
NAMED_STATUS_TEXT = {
    "READY": "경기 전",
    "IN_PROGRESS": "진행 중",
    "BREAK_TIME": "하프타임",
    "FINAL": "경기 종료",
    "CANCEL": "경기 취소",
    "CANCELED": "경기 취소",
    "CANCELLED": "경기 취소",
    "CUT": "경기 취소",
    "POSTPONED": "경기 연기",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://m.sports.naver.com/"})
    return s


def _aliases() -> dict[str, list[str]]:
    """네이버 팀명 → 그 팀을 가리키는 모든 이름(프로토 표기 포함)."""
    raw = load_document("processed_team_map", TEAM_MAP) or {}
    out: dict[str, list[str]] = {}
    for league in raw.values():
        # team_map 은 {프로토명: 네이버명} 형태다
        for proto, naver in league.items():
            out.setdefault(naver, []).append(proto)
    return out


def fetch(s: requests.Session, league: str, day: str) -> list[dict]:
    up, cid = CATS[league]
    try:
        r = s.get(API, params={
            "fields": "basic,statusNum", "upperCategoryId": up,
            "categoryId": cid, "fromDate": day, "toDate": day, "size": 200,
        }, timeout=SCHEDULE_TIMEOUT)
        r.raise_for_status()
        return r.json().get("result", {}).get("games", []) or []
    except Exception as e:                            # noqa: BLE001
        print(f"  {league} {day} 실패: {type(e).__name__}", flush=True)
        raise


def fetch_named(s: requests.Session, day: str) -> dict:
    """NAMED의 날짜별 인기 경기 목록. 종목별 배열을 그대로 돌려준다."""
    try:
        r = s.get(NAMED_API, params={"date": day, "tomorrow-game-flag": "true"},
                  timeout=SCHEDULE_TIMEOUT)
        r.raise_for_status()
        return r.json() or {}
    except Exception as e:                            # noqa: BLE001
        print(f"  NAMED {day} 실패: {type(e).__name__}", flush=True)
        raise


def _score(team: dict) -> int | float | None:
    periods = team.get("periodData") or []
    values = [p.get("score") for p in periods if p.get("score") is not None]
    if values:
        return sum(values)
    value = team.get("score")
    return value if value not in (None, "") else None


def _regulation_score(team: dict) -> int | None:
    """Soccer settlement uses periods 1+2, excluding extra time and shootouts."""
    periods = team.get("periodData") or []
    scores = []
    for period in (1, 2):
        rows = [row for row in periods if row.get("period") == period]
        if len(rows) != 1:
            return None
        score = rows[0].get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        if score < 0 or not float(score).is_integer():
            return None
        scores.append(int(score))
    return sum(scores)


def named_soccer_clock(raw: dict) -> dict:
    """NAMED 축구 중계의 최신 이벤트 시각을 전·후반 경과 분으로 바꾼다.

    `displayTime`은 HH:MM 모양이지만 시:분이 아니라 누적 경기 분이다.
    예: 01:23 = 83분 = 후반 38분.
    """
    status = str(raw.get("gameStatus") or "").upper()
    period = int(raw.get("period") or 0)
    if status == "BREAK_TIME":
        return {"period": period or 2, "elapsed_minute": 45,
                "phase": "halftime", "label": "하프타임"}
    if status != "IN_PROGRESS":
        return {}
    broadcast = raw.get("broadcast") or {}
    display = str(broadcast.get("displayTime") or raw.get("displayTime") or "")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", display)
    if not match:
        return {"period": period, "phase": "live", "label": "진행 중"}
    elapsed = int(match.group(1)) * 60 + int(match.group(2))
    if period <= 1:
        minute = max(1, elapsed)
        return {"period": 1, "elapsed_minute": elapsed,
                "phase": "first_half", "label": f"전반 {minute}분"}
    minute = max(1, elapsed - 45)
    return {"period": period, "elapsed_minute": elapsed,
            "phase": "second_half", "label": f"후반 {minute}분"}


def normalize_named_game(raw: dict, sport: str) -> dict:
    """NAMED 한 경기를 프론트가 이미 읽는 live_scores 공통 형식으로 바꾼다."""
    teams = raw.get("teams") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    original_status = str(raw.get("gameStatus") or "").upper()
    status = NAMED_STATUS.get(original_status, original_status or "BEFORE")
    start = str(raw.get("startDatetime") or "")
    if start and not re.search(r"(?:Z|[+-]\d\d:\d\d)$", start):
        start += "+09:00"
    league = raw.get("league") or {}
    finished = status in RESULT_STATUSES
    clock = named_soccer_clock(raw) if sport == "soccer" else {}
    rec = {
        "source": "named",
        "sport": NAMED_SPORTS[sport][0],
        "league": league.get("shortName") or league.get("name") or NAMED_SPORTS[sport][1],
        "game_id": f"named:{raw.get('id')}",
        "start": start,
        "md": start[5:10].replace("-", "."),
        "home": home.get("name"), "away": away.get("name"),
        "home_alias": [x for x in (home.get("shortName"),) if x],
        "away_alias": [x for x in (away.get("shortName"),) if x],
        "home_score": _score(home), "away_score": _score(away),
        "status": status,
        "status_text": clock.get("label") or NAMED_STATUS_TEXT.get(original_status, "상태 확인 중"),
        "finished": finished,
        "terminal": status in TERMINAL_STATUSES,
        "cancelled": status in {"CANCEL", "CANCELED", "CANCELLED"},
        "postponed": status == "POSTPONED",
    }
    if clock:
        rec["clock"] = clock
    if sport == "baseball" and status == "STARTED":
        period = raw.get("period")
        division = str(raw.get("inningDivision") or "").upper()
        if type(period) is int and period > 0 and division in ("TOP", "BOTTOM"):
            rec["inning"] = period
            rec["batting_side"] = "away" if division == "TOP" else "home"
            rec["status_text"] = f"{period}회{'초' if division == 'TOP' else '말'}"
        # The public broadcast provides a current total while periodData can
        # omit the in-progress inning. Never infer current pitcher from starter.
        score = (raw.get("broadcast") or {}).get("score") or {}
        for side in ("home", "away"):
            value = score.get(side)
            if type(value) is int and value >= 0:
                rec[f"{side}_score"] = value
    if sport == "soccer" and finished:
        regular_time_score = [_regulation_score(home), _regulation_score(away)]
        if all(score is not None for score in regular_time_score):
            rec["regular_time_score"] = regular_time_score
    if status == "BEFORE":
        rec["home_score"] = rec["away_score"] = None
    return rec


def _team_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _team_similarity(left: str, right: str) -> float:
    a, b = _team_key(left), _team_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        return .96
    return difflib.SequenceMatcher(None, a, b).ratio()


def _team_similarity_with_aliases(proto_name: str, game: dict, side: str) -> float:
    """Compare a Proto label against every NAMED label for the same team."""
    names = [game.get(side), *(game.get(f"{side}_alias") or [])]
    return max((_team_similarity(proto_name, name) for name in names), default=0.0)


def _proto_games() -> list[dict]:
    payload = load_artifact("picks_v2", PICKS) or {}
    return [*payload.get("live", []), *payload.get("past", [])]


def add_proto_aliases(named_games: list[dict], proto_games: list[dict]) -> int:
    """NAMED 정식 팀명에 프로토 축약명을 붙여 프론트의 정확 키 조인을 살린다."""
    candidates: dict[tuple[str, str], list[dict]] = {}
    for game in proto_games:
        md = str(game.get("date") or "")[:5]
        candidates.setdefault((str(game.get("sport") or ""), md), []).append(game)

    matched = 0
    for game in named_games:
        ranked = []
        for proto in candidates.get((game.get("sport"), game.get("md")), []):
            # NAMED's canonical name can omit the city used by Proto (for example
            # 반라우레 vs 하치노헤). shortName is retained as an alias, so score
            # every supplied label instead of comparing only the canonical name.
            hs = _team_similarity_with_aliases(proto.get("home"), game, "home")
            aws = _team_similarity_with_aliases(proto.get("away"), game, "away")
            named_time = str(game.get("start") or "")[11:16]
            match = re.search(r"(\d{2}:\d{2})", str(proto.get("date") or ""))
            same_time = bool(match and named_time == match.group(1))
            # 축약이 심한 프로토 팀명은 문자열 점수만 낮을 수 있다. 날짜와 킥오프가
            # 모두 같으면 문턱을 낮추되 양 팀 중 하나라도 전혀 다르면 거부한다.
            floor = .25 if same_time else .45
            if min(hs, aws) >= floor:
                # Matching date, sport and exact kickoff is strong independent evidence.
                # A .25 bonus keeps common one-letter Proto abbreviations (U/W) from
                # missing the otherwise conservative aggregate threshold.
                ranked.append((hs + aws + (.25 if same_time else 0), min(hs, aws), proto))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        if not ranked or ranked[0][0] < 1.15:
            continue
        # 애매한 동명이인/축약 매칭은 붙이지 않는다.
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .08:
            continue
        proto = ranked[0][2]
        for side in ("home", "away"):
            name = proto.get(side)
            aliases = game[f"{side}_alias"]
            if name and name != game.get(side) and name not in aliases:
                aliases.append(name)
        matched += 1
    return matched


def baseball_situation(payload: dict) -> dict:
    """경기 폴링 응답에서 현재 타석과 주자 상황만 정규화한다."""
    relay = (payload.get("result") or {}).get("textRelayData") or {}
    base_info = relay.get("baseInfo") or {}
    count = base_info.get("ballCount") or {}
    if not count:
        return {}
    batting_side = "away" if str(relay.get("homeOrAway")) == "0" else "home"
    pitcher = base_info.get("homePitcher") if batting_side == "away" else base_info.get("awayPitcher")
    bases = {}
    for number, key in ((1, "first"), (2, "second"), (3, "third")):
        runner = count.get(f"base{number}") or None
        runner_id = count.get(f"base{number}Id") or None
        bases[key] = {"occupied": bool(runner or runner_id), "runner": runner,
                      "runner_id": runner_id}
    next_player = base_info.get("nextPlayer") or {}
    return {
        "batting_side": batting_side,
        "batter": count.get("batter") or None,
        "batter_id": count.get("batterId") or None,
        "pitcher": pitcher or None,
        "balls": count.get("b"), "strikes": count.get("s"), "outs": count.get("o"),
        "bases": bases,
        "next_batter": next_player.get("player") or None,
        "on_deck": next_player.get("nextPlayer") or None,
    }


def fetch_situation(s: requests.Session, game_id: str) -> dict:
    try:
        r = s.get(POLLING_API.format(game_id=game_id), timeout=SITUATION_TIMEOUT)
        r.raise_for_status()
        return baseball_situation(r.json())
    except Exception as e:                            # noqa: BLE001
        print(f"  상세 {game_id} 실패: {type(e).__name__}", flush=True)
        return {}


def _previous_games() -> list[dict]:
    """DB에 남은 최근 종료 상태를 읽어 다음 관측에서 보존한다."""
    previous = load_artifact("live_scores", OUT) or {}
    games = []
    for game in previous.get("games") or []:
        game = dict(game)
        # Legacy rows were last observed no later than the old document. Never
        # stamp retained rows with this run's generated_at.
        game.setdefault("observed_at", previous.get("generated_at"))
        games.append(game)
    return games


def _alias_event_key(game: dict) -> tuple | None:
    """Strict provider identity; aliases themselves must never prove a match."""
    fields = ("source", "game_id", "start", "home", "away")
    values = tuple(game.get(field) for field in fields)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return None
    # Normalizers can emit 'named:None' for an upstream row without an ID.
    if values[1].rsplit(":", 1)[-1].lower() in ("", "none", "null"):
        return None
    try:
        datetime.fromisoformat(values[2])
    except ValueError:
        return None
    if len(values[2]) < 16:  # A date alone cannot distinguish doubleheaders.
        return None
    # Keep the full kickoff (including year, seconds and offset), canonical
    # side names and competition exact. Uncertain identities can wait for the
    # optional matching pass; no fuzzy comparison belongs before first save.
    return (*values, game.get("sport"), game.get("league"), game.get("md"))


def _carry_verified_aliases(current: list[dict], previous: list[dict]) -> list[dict]:
    """Carry only alias lists, never old scores, status, clocks or timestamps."""
    by_event: dict[tuple, dict[str, list]] = {}
    for game in previous:
        key = _alias_event_key(game)
        if key is not None:
            aliases = by_event.setdefault(key, {"home": [], "away": []})
            for side in aliases:
                aliases[side].extend(game.get(f"{side}_alias") or [])
    games = []
    for game in current:
        saved = by_event.get(_alias_event_key(game))
        if saved is not None:
            game = dict(game)
            for side in saved:
                game[f"{side}_alias"] = list(dict.fromkeys(
                    name for name in [*(game.get(f"{side}_alias") or []), *saved[side]]
                    if isinstance(name, str) and name.strip() and name != game.get(side)
                ))
        games.append(game)
    return games


def merge_recent_games(current: list[dict], previous: list[dict], now: datetime) -> list[dict]:
    """최신 관측을 우선하되 조회 범위 밖의 최근 종료·취소 기록은 보존한다."""
    cutoff = (now - timedelta(days=HISTORY_DAYS)).date()
    # Both today-first checkpoints and full documents pass here. Restore
    # verified aliases before cross-source deduplication can change the ID.
    current = _carry_verified_aliases(current, previous)
    merged = {str(g.get("game_id")): g for g in deduplicate_games(current) if g.get("game_id")}
    for game in previous:
        game_id = str(game.get("game_id") or "")
        if not game_id or game_id in merged or game.get("status") not in TERMINAL_STATUSES:
            continue
        try:
            played = datetime.fromisoformat(str(game.get("start") or "")[:10]).date()
        except ValueError:
            continue
        if played >= cutoff:
            merged[game_id] = game
    return sorted(deduplicate_games(list(merged.values())), key=lambda x: x.get("start") or "")


def _identity_text(value: object) -> str:
    """소스별 공백·구두점 차이를 없앤 경기 식별용 문자열."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _team_names(game: dict, side: str) -> set[str]:
    names = [game.get(side), *(game.get(f"{side}_alias") or [])]
    return {cleaned for value in names if (cleaned := _identity_text(value))}


def _same_physical_game(left: dict, right: dict) -> bool:
    """네이버·NAMED·옛 ID가 달라도 실제 같은 경기인지 판별한다."""
    left_id = re.sub(r"^(?:naver|named):", "", str(left.get("game_id") or ""))
    right_id = re.sub(r"^(?:naver|named):", "", str(right.get("game_id") or ""))
    if left_id and left_id == right_id:
        return True
    if _identity_text(left.get("league")) != _identity_text(right.get("league")):
        return False
    # 같은 날 같은 팀의 더블헤더는 시작 시각으로 반드시 분리한다.
    if str(left.get("start") or "")[:16] != str(right.get("start") or "")[:16]:
        return False
    return bool(_team_names(left, "home") & _team_names(right, "home")) and bool(
        _team_names(left, "away") & _team_names(right, "away")
    )


def _merge_duplicate(preferred: dict, other: dict) -> dict:
    """실시간 야구 상황이 풍부한 네이버 값을 우선하고 별칭은 합친다."""
    if bool(preferred.get("stale")) != bool(other.get("stale")):
        if preferred.get("stale"):
            preferred, other = other, preferred
    else:
        def phase(game):
            if game.get("status") in TERMINAL_STATUSES:
                return 2
            return 1 if game.get("status") == "STARTED" else 0
        if phase(other) > phase(preferred):
            preferred, other = other, preferred
        elif (phase(other) == phase(preferred)
              and preferred.get("source") != "naver" and other.get("source") == "naver"):
            preferred, other = other, preferred
    merged = {**other, **preferred}
    merged["stale"] = bool(preferred.get("stale"))
    # A failed-source row must not donate its outdated batter/runner state to a
    # successful source which only supplied scores.
    if (other.get("stale") and not preferred.get("stale")
            or preferred.get("status") != "STARTED" or other.get("status") != "STARTED"):
        for key in ("batting_side", "batter", "batter_id", "pitcher", "balls", "strikes",
                    "outs", "bases", "next_batter", "on_deck", "situation_observed_at"):
            if key not in preferred:
                merged.pop(key, None)
    for side in ("home", "away"):
        aliases = [*preferred.get(f"{side}_alias", []), other.get(side),
                   *other.get(f"{side}_alias", [])]
        merged[f"{side}_alias"] = list(dict.fromkeys(
            value for value in aliases if value and value != merged.get(side)
        ))
    return merged


def deduplicate_games(games: list[dict]) -> list[dict]:
    """소스 ID가 다른 동일 경기를 하나로 합치고 실제 더블헤더는 유지한다."""
    unique: list[dict] = []
    by_id: dict[str, set[int]] = {}
    by_start: dict[tuple[str, str], set[int]] = {}

    def keys(game):
        return (re.sub(r"^(?:naver|named):", "", str(game.get("game_id") or "")),
                (_identity_text(game.get("league")), str(game.get("start") or "")[:16]))

    def index(game, position):
        game_id, start = keys(game)
        if game_id:
            by_id.setdefault(game_id, set()).add(position)
        by_start.setdefault(start, set()).add(position)

    for game in games:
        game_id, start = keys(game)
        # Identity equality or equal league/kickoff are necessary conditions in
        # _same_physical_game. Do not compare all 45 days of unrelated history.
        candidates = by_id.get(game_id, set()) | by_start.get(start, set())
        duplicate_at = next((i for i in sorted(candidates)
                             if _same_physical_game(unique[i], game)), None)
        if duplicate_at is None:
            index(game, len(unique))
            unique.append(game)
        else:
            unique[duplicate_at] = _merge_duplicate(unique[duplicate_at], game)
            index(unique[duplicate_at], duplicate_at)
    return unique


def _schedule_job(job: tuple[str, str], deadline: float | None = None) -> dict:
    league, day = job
    # requests.Session is not shared between worker threads.
    result = {"source": "named" if league == "named" else "naver", "league": league, "day": day}
    if deadline is not None and time.monotonic() >= deadline:
        return {**result, "payload": None, "observed_at": None, "error": "budget_exhausted"}
    try:
        with _session() as session:
            payload = fetch_named(session, day) if league == "named" else fetch(session, league, day)
        # Retrieval time only: these responses expose no verified provider
        # update timestamp, so this is not a claim about upstream freshness.
        result.update(payload=payload, observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      error=None)
    except Exception as exc:  # One source failure must not erase other sources.
        result.update(payload=None, observed_at=None, error=f"{type(exc).__name__}: {exc}")
    return result


def collect_schedules(days: list[str], deadline: float | None = None) -> list[dict]:
    """Bound network fan-out while preserving deterministic provider/day order."""
    jobs = [(league, day) for day in days for league in (*CATS, "named")]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        return list(pool.map(lambda job: _schedule_job(job, deadline), jobs))


def _situation_job(game_id: str, deadline: float | None = None) -> dict:
    if deadline is not None and time.monotonic() >= deadline:
        return {}
    with _session() as session:
        situation = fetch_situation(session, game_id)
    if situation:
        situation["situation_observed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return situation


def enrich_situations(games: list[dict], deadline: float | None = None) -> None:
    live = [game for game in games if game.get("source") == "naver"
            and not game.get("stale") and game.get("status") == "STARTED"
            and game.get("league") in ("KBO", "MLB", "NPB")][:SITUATION_LIMIT]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        situations = pool.map(lambda game_id: _situation_job(game_id, deadline),
                              [game["game_id"].split(":", 1)[1] for game in live])
        for game, situation in zip(live, situations):
            game.update(situation)


def normalize_naver_game(g: dict, league: str, alias: dict, observed_at: str) -> dict:
    st = g.get("statusCode") or ""
    if g.get("cancel") and st not in TERMINAL_STATUSES:
        st = "CANCEL"
    home, away = g.get("homeTeamName"), g.get("awayTeamName")
    start = g.get("gameDateTime") or ""
    return {
        "source": "naver", "sport": "sc" if league == "K리그" else "bs", "league": league,
        "game_id": f"naver:{g.get('gameId')}", "start": start,
        "md": start[5:10].replace("-", "."),
        "home": home, "away": away, "home_alias": alias.get(home, []), "away_alias": alias.get(away, []),
        "home_score": None if st == "BEFORE" else g.get("homeTeamScore"),
        "away_score": None if st == "BEFORE" else g.get("awayTeamScore"),
        "status": st, "status_text": "경기 전" if st == "BEFORE" else g.get("statusInfo"),
        "finished": st in RESULT_STATUSES, "terminal": st in TERMINAL_STATUSES,
        "cancelled": st in {"CANCEL", "CANCELED", "CANCELLED"}, "postponed": st == "POSTPONED",
        "observed_at": observed_at, "stale": False,
    }


def retained_failed_games(previous: list[dict], results: list[dict]) -> list[dict]:
    failed = {(r["source"], r["league"], r["day"]) for r in results if r["error"]}
    return [{**g, "stale": True} for g in previous
            if (g.get("source"), "named" if g.get("source") == "named" else g.get("league"),
                str(g.get("start") or "")[:10]) in failed]


def schedule_games(results: list[dict], alias: dict) -> list[dict]:
    games = []
    # Overlapping NAMED dates can repeat one ID: retain the latest observation.
    for result in sorted(results, key=lambda r: r["observed_at"] or ""):
        if result["error"]:
            continue
        if result["source"] == "naver":
            games.extend(normalize_naver_game(g, result["league"], alias, result["observed_at"])
                         for g in result["payload"])
        else:
            for sport in NAMED_SPORTS:
                for raw in result["payload"].get(sport) or []:
                    game = {**normalize_named_game(raw, sport),
                            "observed_at": result["observed_at"], "stale": False}
                    # Exact known aliases are cheap and available before the
                    # checkpoint; fuzzy Proto matching remains optional later.
                    for side in ("home", "away"):
                        known = [game.get(side), *game[f"{side}_alias"]]
                        game[f"{side}_alias"] = list(dict.fromkeys([
                            *game[f"{side}_alias"],
                            *(name for key in known for name in alias.get(key, [])),
                        ]))
                    games.append(game)
    return list({g["game_id"]: g for g in games}.values())


def build_document(games: list[dict], previous: list[dict], now: datetime,
                   results: list[dict], *, partial: bool = False) -> dict:
    """generated_at is assembly time; only row observed_at describes freshness."""
    retained = retained_failed_games(previous, results)
    uniq = merge_recent_games([*games, *retained], previous, now)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_games": len(uniq), "n_live": sum(g.get("status") == "STARTED" and not g.get("stale") for g in uniq),
        "games": uniq, "partial": partial or any(r["error"] for r in results),
        "source_status": [{k: v for k, v in result.items() if k != "payload"} for result in results],
    }


def main() -> int:
    started = time.monotonic()
    deadline = started + FETCH_BUDGET_SECONDS
    alias = _aliases()
    previous = _previous_games()
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    other_days = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 1, -2, -3)]
    # Same deadline/checkpoint principle as PR #169, with today first and row
    # timestamps preserved. No historical scan or optional relay can block the
    # first successful score publication.
    results = collect_schedules([today], deadline)
    if not any(r["error"] is None for r in results):
        raise RuntimeError("all current-day score sources failed; previous artifact unchanged")
    pending = [{"source": "named" if league == "named" else "naver", "league": league,
                "day": day, "error": "not_yet_refreshed", "observed_at": None}
               for day in other_days for league in (*CATS, "named")]
    games = schedule_games(results, alias)
    checkpoint = build_document(games, previous, now, [*results, *pending], partial=True)
    persist_artifact("live_scores", checkpoint, OUT, indent=None)
    print(f"실시간 점수 현재일 저장 · {len(games)}건 · {time.monotonic() - started:.1f}s", flush=True)

    results.extend(collect_schedules(other_days, deadline))
    games = schedule_games(results, alias)
    if time.monotonic() < deadline:
        named = [g for g in games if g.get("source") == "named"]
        add_proto_aliases(named, _proto_games())
    document = build_document(games, previous, now, results)
    # Persist all scores before optional pitch-by-pitch requests. A killed relay
    # phase leaves correct score timestamps in the already stored artifact.
    persist_artifact("live_scores", document, OUT, indent=None)
    enrich_situations(document["games"], deadline)
    document["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    persist_artifact("live_scores", document, OUT, indent=None)
    print(f"경기 {document['n_games']}건 · 진행중 {document['n_live']}"
          f" · 부분={document['partial']} · {time.monotonic() - started:.1f}s → {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
