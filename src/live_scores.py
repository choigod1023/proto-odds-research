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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from runtime_db import load_artifact, persist_artifact

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
    if not TEAM_MAP.exists():
        return {}
    raw = json.loads(TEAM_MAP.read_text(encoding="utf-8"))
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
        }, timeout=20)
        r.raise_for_status()
        return r.json().get("result", {}).get("games", []) or []
    except Exception as e:                            # noqa: BLE001
        print(f"  {league} {day} 실패: {type(e).__name__}", flush=True)
        return []


def fetch_named(s: requests.Session, day: str) -> dict:
    """NAMED의 날짜별 인기 경기 목록. 종목별 배열을 그대로 돌려준다."""
    try:
        r = s.get(NAMED_API, params={"date": day, "tomorrow-game-flag": "true"},
                  timeout=20)
        r.raise_for_status()
        return r.json() or {}
    except Exception as e:                            # noqa: BLE001
        print(f"  NAMED {day} 실패: {type(e).__name__}", flush=True)
        return {}


def _score(team: dict) -> int | float | None:
    periods = team.get("periodData") or []
    values = [p.get("score") for p in periods if p.get("score") is not None]
    if values:
        return sum(values)
    value = team.get("score")
    return value if value not in (None, "") else None


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
        r = s.get(POLLING_API.format(game_id=game_id), timeout=12)
        r.raise_for_status()
        return baseball_situation(r.json())
    except Exception as e:                            # noqa: BLE001
        print(f"  상세 {game_id} 실패: {type(e).__name__}", flush=True)
        return {}


def _previous_games() -> list[dict]:
    """DB에 남은 최근 종료 상태를 읽어 다음 관측에서 보존한다."""
    previous = load_artifact("live_scores", OUT)
    return (previous or {}).get("games") or []


def merge_recent_games(current: list[dict], previous: list[dict], now: datetime) -> list[dict]:
    """최신 관측을 우선하되 조회 범위 밖의 최근 종료·취소 기록은 보존한다."""
    cutoff = (now - timedelta(days=HISTORY_DAYS)).date()
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
    if preferred.get("source") != "naver" and other.get("source") == "naver":
        preferred, other = other, preferred
    merged = {**other, **preferred}
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
    for game in games:
        duplicate_at = next((index for index, saved in enumerate(unique)
                             if _same_physical_game(saved, game)), None)
        if duplicate_at is None:
            unique.append(game)
        else:
            unique[duplicate_at] = _merge_duplicate(unique[duplicate_at], game)
    return unique


def main() -> int:
    s = _session()
    alias = _aliases()
    now = datetime.now(KST)
    # 최근 3일을 다시 확인해 마지막 요청 실패·우천 연기처럼 경계에서 바뀐 상태도 회수한다.
    days = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-3, -2, -1, 0, 1)]

    games = []
    # 네이버는 보조 원천이다. 뒤에서 NAMED를 추가해 같은 프론트 키가 겹칠 때
    # 더 넓은 종목을 커버하는 NAMED 관측이 우선되게 한다.
    for league in CATS:
        for day in days:
            for g in fetch(s, league, day):
                st = g.get("statusCode") or ""
                if g.get("cancel") and st not in TERMINAL_STATUSES:
                    st = "CANCEL"
                home, away = g.get("homeTeamName"), g.get("awayTeamName")
                start = g.get("gameDateTime") or ""
                rec = {
                    "source": "naver",
                    "sport": "sc" if league == "K리그" else "bs",
                    "league": league,
                    "game_id": f"naver:{g.get('gameId')}",
                    "start": start,
                    # ⚠️ 팀 조합만으로는 경기를 못 가린다. MLB 는 같은 팀끼리
                    #    3~4연전을 하므로 어제/오늘 경기가 뭉개진다(실제로 정산
                    #    경기 55건 중 37건이 어긋났다). 날짜를 키에 넣어야 한다.
                    #    네이버 gameDateTime 은 이미 KST 다.
                    "md": start[5:10].replace("-", "."),      # '07.31'
                    "home": home, "away": away,
                    # 프론트가 프로토 표기로도 찾을 수 있게 별칭을 같이 준다
                    "home_alias": alias.get(home, []),
                    "away_alias": alias.get(away, []),
                    "home_score": g.get("homeTeamScore"),
                    "away_score": g.get("awayTeamScore"),
                    "status": st,                     # BEFORE / STARTED / RESULT ...
                    "status_text": g.get("statusInfo"),
                    "finished": st in RESULT_STATUSES,
                    "terminal": st in TERMINAL_STATUSES,
                    "cancelled": st in {"CANCEL", "CANCELED", "CANCELLED"},
                    "postponed": st == "POSTPONED",
                }
                if st == "STARTED" and league in ("KBO", "MLB", "NPB") and rec["game_id"]:
                    rec.update(fetch_situation(s, str(g.get("gameId"))))
                # 경기 전이면 0-0 이 찍혀 나온다 — 점수처럼 보이면 안 된다
                if st == "BEFORE":
                    rec["home_score"] = rec["away_score"] = None
                games.append(rec)

    named_games = []
    for day in days:
        payload = fetch_named(s, day)
        for sport in NAMED_SPORTS:
            for raw in payload.get(sport) or []:
                named_games.append(normalize_named_game(raw, sport))

    # tomorrow-game-flag 때문에 이웃 날짜 응답이 겹칠 수 있다.
    named_games = list({g["game_id"]: g for g in named_games}.values())
    alias_n = add_proto_aliases(named_games, _proto_games())
    games.extend(named_games)

    # 날짜 경계뿐 아니라 네이버·NAMED의 서로 다른 ID도 실제 경기 기준으로 합친다.
    uniq = deduplicate_games(games)
    uniq = merge_recent_games(uniq, _previous_games(), now)
    live_n = sum(1 for g in uniq if g.get("status") == "STARTED")

    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_games": len(uniq), "n_live": live_n, "games": uniq,
    }
    persist_artifact("live_scores", document, OUT, indent=None)

    done = sum(1 for g in uniq if g["finished"])
    named_n = sum(1 for g in uniq if g.get("source") == "named")
    print(f"경기 {len(uniq)}건(NAMED {named_n}) · 프로토 매칭 {alias_n}"
          f" · 진행중 {live_n} · 종료 {done} → {OUT}")
    for g in uniq:
        if not g["finished"] and g["status"] != "BEFORE":
            print(f"  [{g['league']}] {g['away']} {g['away_score']}"
                  f" : {g['home_score']} {g['home']} ({g['status_text']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
