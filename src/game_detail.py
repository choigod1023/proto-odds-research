"""경기 상세 수집 — 투수 개인기록(야구) · 라인업(축구).

왜 필요한가
------------
· 야구: `pitcher_impact.py` 는 "그 투수 등판 경기에서 **팀이** 내준 점수"를 대리지표로 썼다.
  거기엔 불펜 실점이 섞여 선발을 제대로 못 잰다. 3개 리그에서 효과가 재현되지 않은 것도
  지표가 거칠어서일 수 있다. → **자책점·이닝**으로 교체한다.
· 축구: 라인업은 경기 1시간 전에야 공개된다. 배당이 굳는 것보다 훨씬 늦으므로
  **정보 시차가 가장 클 후보**다. 그런데 스케줄 API 에는 없다.

엔드포인트 (2026-07-27 확인)
    야구  GET /schedule/games/{gameId}/record
          → result.recordData.pitchersBoxscore.{home,away}[]
            inn(이닝) er(자책점) r(실점) kk(삼진) bb(볼넷) hr era pcode name
    축구  GET /schedule/games/{gameId}/lineup
          → result.lineUpData.lineup.{home,away}.{players, formation, row}

⚠️ 경기당 1요청이라 비용이 크다. 캐시를 두고 이미 받은 경기는 건너뛴다.
   비상업 연구 목적, 요청 간격 준수.

사용:
    python src/game_detail.py baseball kbo 2026     # KBO 2026 투수기록
    python src/game_detail.py soccer kleague        # K리그 라인업
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "detail"
API = "https://api-gw.sports.naver.com"
GAP = 1.0

# 종목 → (upperCategoryId, categoryId) 목록
CATS = {
    "kbo": ("kbaseball", "kbo"), "mlb": ("wbaseball", "mlb"),
    "npb": ("wbaseball", "npb"),
    "kleague": ("kfootball", "kleague"), "mls": ("wfootball", "mls"),
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json", "Referer": "https://m.sports.naver.com/"})
    return s


def list_games(sess, league: str, y0: int, y1: int) -> list[dict]:
    up, cid = CATS[league]
    out, d = [], date(y0, 1, 1)
    end = min(date(y1, 12, 31), date.today())
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        hi = min(nxt - timedelta(days=1), end)
        try:
            r = sess.get(f"{API}/schedule/games", params={
                "fields": "basic,statusNum", "upperCategoryId": up,
                "categoryId": cid, "fromDate": d.isoformat(),
                "toDate": hi.isoformat(), "size": 500}, timeout=25)
            gs = r.json().get("result", {}).get("games", [])
            out += [g for g in gs if not g.get("cancel")]
        except Exception as e:                       # noqa: BLE001
            print(f"  일정 {d}~{hi} 오류 {type(e).__name__}", flush=True)
        d = nxt
        time.sleep(0.6)
    return out


def parse_baseball(res: dict) -> dict | None:
    rd = (res or {}).get("recordData") or {}
    pb = rd.get("pitchersBoxscore") or {}
    if not pb:
        return None
    keep = ("pcode", "name", "inn", "er", "r", "kk", "bb", "hit", "hr", "era",
            "bf", "wls")
    out = {}
    for side in ("home", "away"):
        rows = pb.get(side) or []
        out[side] = [{k: p.get(k) for k in keep} for p in rows]
    return out or None


def parse_soccer(res: dict) -> dict | None:
    ld = (res or {}).get("lineUpData") or {}
    lu = ld.get("lineup") or {}
    if not lu:
        return None
    out = {}
    for side in ("home", "away"):
        d = lu.get(side) or {}
        players = d.get("players") or []
        flat = []
        for row in players:
            items = row if isinstance(row, list) else [row]
            for p in items:
                if isinstance(p, dict):
                    # 실제 필드명: name / pos / shirtNumber / positionOrder
                    flat.append({k: p.get(k) for k in
                                 ("playerId", "name", "pos", "shirtNumber",
                                  "positionOrder", "changed", "goal",
                                  "assists", "yellowCardCnt", "redCardCnt")})
        out[side] = {"formation": d.get("formation"), "players": flat}
    subs = ld.get("substitution") or {}
    out["substitution"] = {s: subs.get(s) for s in ("home", "away")}
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    kind, league = argv[1], argv[2]
    yrs = [int(a) for a in argv[3:] if a.isdigit()]
    y0 = yrs[0] if yrs else 2023
    y1 = yrs[1] if len(yrs) > 1 else date.today().year
    path = "record" if kind == "baseball" else "lineup"
    parse = parse_baseball if kind == "baseball" else parse_soccer

    RAW.mkdir(parents=True, exist_ok=True)
    out_file = RAW / f"{league}_{kind}_{y0}_{y1}.json"
    cache = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
    print(f"{league.upper()} {kind} {y0}~{y1} · 기존 캐시 {len(cache)}경기")

    sess = _session()
    games = list_games(sess, league, y0, y1)
    todo = [g for g in games if g.get("gameId") not in cache
            and g.get("statusCode") != "BEFORE"]
    print(f"일정 {len(games)}경기 · 수집 대상 {len(todo)}경기 "
          f"(예상 {len(todo)*GAP/60:.0f}분)", flush=True)

    got = 0
    for i, g in enumerate(todo, 1):
        gid = g["gameId"]
        try:
            r = sess.get(f"{API}/schedule/games/{gid}/{path}", timeout=20)
            if r.status_code == 200:
                d = parse(r.json().get("result"))
                if d:
                    cache[gid] = {"gameId": gid, "date": g.get("gameDate"),
                                  "home": g.get("homeTeamName"),
                                  "away": g.get("awayTeamName"),
                                  "home_score": g.get("homeTeamScore"),
                                  "away_score": g.get("awayTeamScore"),
                                  "data": d}
                    got += 1
        except Exception as e:                       # noqa: BLE001
            print(f"  {gid} 오류 {type(e).__name__}", flush=True)
            time.sleep(2)
        if i % 100 == 0:
            out_file.write_text(json.dumps(cache, ensure_ascii=False),
                                encoding="utf-8")
            print(f"  {i}/{len(todo)} · 확보 {got}", flush=True)
        time.sleep(GAP)

    out_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n완료 · 총 {len(cache)}경기 (신규 {got}) → {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
