"""KBO 선발투수 수집 (네이버 스포츠 공개 API).

왜 이게 필요한가
-----------------
`variable_impact.py` 측정에서 **야구는 팀 단위 변수 9개가 전부 Elo를 못 넘었다.**
팀 정보가 고갈됐다는 뜻이고, 남은 정보는 선수 단위에만 있다.
야구에서 그 첫 번째가 선발투수다.

엔드포인트 (2026-07-27 확인)
    GET https://api-gw.sports.naver.com/schedule/games
        ?fields=basic,statusNum,homeStarterName,awayStarterName
        &upperCategoryId=kbaseball&categoryId=kbo
        &fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD&size=...

    · 정산된 경기는 homeStarterName/awayStarterName 이 100% 채워져 있다
    · **경기 전 경기는 비어 있다가 예고 시점에 채워진다** → Q2(정보 시차)의 관측 대상

⚠️ 비상업 연구 목적. 월 단위로 끊어 요청하고 간격을 둔다.

사용:
    python src/kbo_starters.py            # 2023~현재 백필
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
API = "https://api-gw.sports.naver.com/schedule/games"
GAP = 1.2


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://m.sports.naver.com/",
    })
    return s


# 리그 → (upperCategoryId, categoryId). upperCategoryId 가 리그마다 다르다.
LEAGUES = {"kbo": ("kbaseball", "kbo"), "mlb": ("wbaseball", "mlb"),
           "npb": ("wbaseball", "npb")}


def fetch_range(sess: requests.Session, f: date, t: date,
                league: str = "kbo") -> list[dict]:
    up, cid = LEAGUES[league]
    r = sess.get(API, params={
        "fields": "basic,statusNum,homeStarterName,awayStarterName",
        "upperCategoryId": up, "categoryId": cid,
        "fromDate": f.isoformat(), "toDate": t.isoformat(), "size": 500,
    }, timeout=25)
    r.raise_for_status()
    return r.json().get("result", {}).get("games", [])


def month_spans(y0: int, y1: int):
    d = date(y0, 1, 1)
    end = min(date(y1, 12, 31), date.today() + timedelta(days=14))
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    league = next((a for a in args if a in LEAGUES), "kbo")
    yrs = [int(a) for a in args if a.isdigit()]
    y0 = yrs[0] if yrs else 2023
    y1 = yrs[1] if len(yrs) > 1 else date.today().year
    OUT = OUT_DIR / f"{league}_starters.json"
    print(f"리그 {league.upper()} · {y0}~{y1}")

    cache: dict[str, dict] = {}
    if OUT.exists():
        cache = {g["gameId"]: g for g in json.loads(OUT.read_text(encoding="utf-8"))}
        print(f"기존 캐시 {len(cache)}경기")

    sess = _session()
    new = 0
    for f, t in month_spans(y0, y1):
        # KBO 는 3~11월. 비시즌은 건너뛴다
        # KBO 3~11월, MLB 3~11월, NPB 3~11월
        if f.month < 3 or f.month > 11:
            continue
        try:
            games = fetch_range(sess, f, t, league)
        except Exception as e:                       # noqa: BLE001
            print(f"  {f}~{t} 오류 {type(e).__name__}: {e}", flush=True)
            time.sleep(3)
            continue
        got = 0
        for g in games:
            if g.get("cancel") or g.get("suspended"):
                continue
            gid = g.get("gameId")
            if not gid:
                continue
            prev = cache.get(gid)
            # 선발이 채워진 최신 정보로 갱신
            if prev and prev.get("homeStarterName") and not g.get("homeStarterName"):
                continue
            cache[gid] = {
                "gameId": gid, "date": g.get("gameDate"),
                "datetime": g.get("gameDateTime"),
                "home": g.get("homeTeamName"), "away": g.get("awayTeamName"),
                "home_score": g.get("homeTeamScore"), "away_score": g.get("awayTeamScore"),
                "home_starter": g.get("homeStarterName") or "",
                "away_starter": g.get("awayStarterName") or "",
                "status": g.get("statusInfo"), "stadium": g.get("stadium"),
            }
            got += 1
        new += got
        print(f"  {f}~{t}: {got}경기", flush=True)
        time.sleep(GAP)

    rows = sorted(cache.values(), key=lambda x: (x["date"] or "", x["gameId"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    filled = sum(1 for r in rows if r["home_starter"] and r["away_starter"])
    print(f"\n총 {len(rows)}경기 · 선발 양쪽 확보 {filled} ({filled/max(len(rows),1):.1%})")
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
