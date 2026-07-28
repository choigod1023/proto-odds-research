"""StatsBomb 오픈데이터 — xG 가 득점보다 나은지 **지금** 판정한다.

왜 이걸 쓰나
------------
K리그 경기별 xG 이력은 세 소스 모두 robots 로 막혀 있다
(FootyStats `/c-dl.php*`, FotMob `/api/*`, Understat `/` 전체).
전진 수집만 하면 필요 표본 441경기에 2026년 11월~2027년이 걸린다.

그런데 **먼저 답해야 할 질문은 "xG 가 득점보다 나은가"** 다. 여기서 지면
K리그 xG 를 기다리거나 돈을 쓸 이유가 없다. 그 질문은 오늘 답할 수 있다.

StatsBomb 오픈데이터는 **명시적 무료 공개**라 robots 문제가 없고,
슛 단위 xG(`shot_statsbomb_xg`)까지 들어 있다. 완전한 시즌:

    라리가 2015/16    380경기
    EPL   2015/16    380경기
    세리에A 2015/16    380경기
    프리그1 2015/16    377경기   → 1,517경기

⚠️ 한계 — 2015/16 이라 **지금 베팅할 모델이 아니라 개념 검증**이다.
   그리고 이 시즌 배당이 없으므로 여기서 답하는 건 "예측력" 까지다.
   시장을 이기는지는 배당을 붙여야 답이 나온다(별도 과제).

출처: StatsBomb Open Data (https://github.com/statsbomb/open-data)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "statsbomb_xg.csv"

RAW = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

# 완전한 시즌만. 부분 시즌(바르셀로나 경기만 모은 것 등)은 팀 폼을 못 만든다.
SEASONS = [
    ("La Liga", "2015/2016"),
    ("Premier League", "2015/2016"),
    ("Serie A", "2015/2016"),
    ("Ligue 1", "2015/2016"),
]

DELAY = 0.15          # raw.githubusercontent 는 정적 CDN — 과하게 조일 필요 없다


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "proto-odds-research (non-commercial study)"})
    return s


def _json(s: requests.Session, url: str, tries: int = 3):
    for i in range(tries):
        try:
            r = s.get(url, timeout=40)
            if r.ok:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:                             # noqa: BLE001
            pass
        time.sleep(2 * (i + 1))
    return None


def match_list(s: requests.Session) -> list[dict]:
    comps = _json(s, f"{RAW}/competitions.json") or []
    out = []
    for name, season in SEASONS:
        c = next((x for x in comps if x["competition_name"] == name
                  and x["season_name"] == season), None)
        if not c:
            print(f"  {name} {season}: 대회 정보 없음")
            continue
        ms = _json(s, f"{RAW}/matches/{c['competition_id']}/{c['season_id']}.json") or []
        for m in ms:
            out.append({
                "match_id": m["match_id"],
                "date": m["match_date"],
                "league": name,
                "home_team": m["home_team"]["home_team_name"],
                "away_team": m["away_team"]["away_team_name"],
                "home_score": m["home_score"], "away_score": m["away_score"],
            })
        print(f"  {name} {season}: {len(ms)}경기")
    return out


def match_xg(s: requests.Session, m: dict) -> dict | None:
    """경기 이벤트에서 팀별 xG 를 합산한다.

    슛 이벤트의 `shot.statsbomb_xg` 를 팀별로 더한다. 페널티는 따로 세어
    npxG(페널티 제외)도 만든다 — 문헌이 실제로 쓰는 건 npxG 다.
    """
    ev = _json(s, f"{RAW}/events/{m['match_id']}.json")
    if not ev:
        return None
    agg = {m["home_team"]: {"xg": 0.0, "npxg": 0.0, "shots": 0, "sot": 0},
           m["away_team"]: {"xg": 0.0, "npxg": 0.0, "shots": 0, "sot": 0}}
    for e in ev:
        if (e.get("type") or {}).get("name") != "Shot":
            continue
        team = (e.get("team") or {}).get("name")
        if team not in agg:
            continue
        sh = e.get("shot") or {}
        xg = float(sh.get("statsbomb_xg") or 0.0)
        agg[team]["xg"] += xg
        if (sh.get("type") or {}).get("name") != "Penalty":
            agg[team]["npxg"] += xg
        agg[team]["shots"] += 1
        out = (sh.get("outcome") or {}).get("name")
        if out in ("Goal", "Saved", "Saved To Post"):
            agg[team]["sot"] += 1

    h, a = agg[m["home_team"]], agg[m["away_team"]]
    if h["shots"] + a["shots"] == 0:
        return None
    r = dict(m)
    for pre, v in (("h", h), ("a", a)):
        for k, val in v.items():
            r[f"{pre}_{k}"] = round(val, 4) if isinstance(val, float) else val
    return r


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    s = _session()
    print("StatsBomb 오픈데이터 — 완전 시즌 목록")
    ms = match_list(s)
    print(f"총 {len(ms)}경기")

    have: set[int] = set()
    if OUT.exists():
        have = set(pd.read_csv(OUT)["match_id"].astype(int))
    todo = [m for m in ms if m["match_id"] not in have]
    if limit:
        todo = todo[:limit]
    print(f"기수집 {len(have)} · 수집 대상 {len(todo)}\n", flush=True)

    rows, fail = [], 0
    for i, m in enumerate(todo, 1):
        r = match_xg(s, m)
        if r is None:
            fail += 1
        else:
            rows.append(r)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)} · 확보 {len(rows)} · 실패 {fail}", flush=True)
            _save(rows)
            rows = []
        time.sleep(DELAY)
    _save(rows)

    df = pd.read_csv(OUT)
    print(f"\n완료 · 총 {len(df):,}경기 → {OUT}")
    print(f"  평균 xG 홈 {df['h_xg'].mean():.2f} / 원정 {df['a_xg'].mean():.2f}")
    print(f"  평균 득점 홈 {df['home_score'].mean():.2f} / 원정 {df['away_score'].mean():.2f}")
    return 0


def _save(rows: list[dict]) -> None:
    if not rows:
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, mode="a" if OUT.exists() else "w",
              header=not OUT.exists(), index=False)


if __name__ == "__main__":
    raise SystemExit(main())
