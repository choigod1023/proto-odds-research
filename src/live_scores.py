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
네이버 스포츠 일정 API. 이미 `game_detail.py`·`info_watch.py` 가 쓰는 검증된 경로다.
한 번 호출에 그날 경기 전부가 점수·상태와 함께 온다:

    homeTeamScore / awayTeamScore / statusCode / statusInfo / gameDateTime

팀명 맞추기
-----------
사이트(프로토 표기)와 네이버 표기가 다르다(`마이말린` vs `마이애미`).
`data/processed/team_map.json` 이 이미 그 대응을 갖고 있으므로 **양방향으로 펴서**
프론트가 어느 쪽 이름으로 찾아도 맞도록 키를 여러 개 넣어 준다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "live_scores.json"
TEAM_MAP = ROOT / "data" / "processed" / "team_map.json"

API = "https://api-gw.sports.naver.com/schedule/games"
POLLING_API = API + "/{game_id}/game-polling"
KST = timezone(timedelta(hours=9))

# 프로토가 파는 것 중 네이버가 커버하는 리그. game_detail.CATS 와 같은 표기.
CATS = {
    "KBO": ("kbaseball", "kbo"),
    "MLB": ("wbaseball", "mlb"),
    "NPB": ("wbaseball", "npb"),
    "K리그": ("kfootball", "kleague"),
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


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


def main() -> int:
    s = _session()
    alias = _aliases()
    now = datetime.now(KST)
    # 어제~내일을 담는다. MLB 는 한국시간으로 새벽에 걸쳐 날짜가 갈린다.
    days = [(now + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)]

    games, live_n = [], 0
    for league in CATS:
        for day in days:
            for g in fetch(s, league, day):
                if g.get("cancel"):
                    continue
                st = g.get("statusCode") or ""
                home, away = g.get("homeTeamName"), g.get("awayTeamName")
                start = g.get("gameDateTime") or ""
                rec = {
                    "league": league,
                    "game_id": g.get("gameId"),
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
                    "finished": st in ("RESULT", "END"),
                }
                if st == "STARTED" and league in ("KBO", "MLB", "NPB") and rec["game_id"]:
                    rec.update(fetch_situation(s, str(rec["game_id"])))
                # 경기 전이면 0-0 이 찍혀 나온다 — 점수처럼 보이면 안 된다
                if st == "BEFORE":
                    rec["home_score"] = rec["away_score"] = None
                elif not rec["finished"]:
                    live_n += 1
                games.append(rec)

    # 같은 경기가 날짜 경계로 두 번 잡힐 수 있다
    seen, uniq = set(), []
    for g in games:
        if g["game_id"] in seen:
            continue
        seen.add(g["game_id"])
        uniq.append(g)
    uniq.sort(key=lambda x: x["start"] or "")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_games": len(uniq), "n_live": live_n, "games": uniq,
    }, ensure_ascii=False), encoding="utf-8")

    done = sum(1 for g in uniq if g["finished"])
    print(f"경기 {len(uniq)}건 · 진행중 {live_n} · 종료 {done} → {OUT}")
    for g in uniq:
        if not g["finished"] and g["status"] != "BEFORE":
            print(f"  [{g['league']}] {g['away']} {g['away_score']}"
                  f" : {g['home_score']} {g['home']} ({g['status_text']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
