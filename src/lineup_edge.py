"""라인업을 보고 베팅하면 프로토를 이기나 — 정보 시차의 실제 창구.

왜 이게 가능한 구조인가
----------------------
    프로토 발매 마감   경기 시작 **10분 전** (경기별)
    축구 라인업 공개   킥오프 **1시간 전**
    프로토 배당        최대 60시간 전에 확정, **86.2% 가 끝까지 안 움직인다**

→ 라인업이 나온 뒤에도 **50분 동안**, 60시간 전 가격에 살 수 있다.

⚠️ 검증 11(정보 시차)에서 "정보의 크기 2.4%p" 라고 닫았는데, 그건 **샤프 마켓의
   24시간 스윙**으로 잰 값이다. 라인업은 거기 안 들어간다 — 샤프 마켓도 1시간 전에야
   반영한다. 즉 그 결론은 라인업 정보에 대해서는 답한 적이 없다.

⚠️ 검증 5(축구 라인업)는 "이번 경기 라인업을 못 쓴다" 로 닫았는데 그것도 틀렸다.
   마감이 10분 전이므로 **쓸 수 있다.**

무엇을 재나
----------
`findings/라인업_2군투입.md` 에서 재둔 효과:
    비주전 투입 0–1명 승률 45.7% → 6명+ 30.1% (완전 단조, 15.6%p)
    팀 내부 비교로도 13팀 중 10팀 같은 방향, 평균 −6.08%p
이걸 **프로토 배당에 대고** ROI 를 잰다. 시장이 이미 알고 있으면 0 이 나온다.

⚠️ 누수 방지 — 주전 XI 는 **그 경기 이전까지의** 선발 횟수로 정한다.
   시즌 전체로 정하면 그 경기 이후 정보가 섞인다.

⚠️ 판정 기준(데이터 보기 전에 적는다)
   · 경기 클러스터 부트스트랩 95%CI 가 0 을 포함하면 기각
   · 연도별 부호가 뒤집히면 기각
   · 프로토 마진(2-way 12%)을 못 넘으면 '통계적으로 유의해도 실전 불가' 로 적는다

사용:
    python3 src/lineup_edge.py
    python3 src/lineup_edge.py --selftest
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LINEUP = ROOT / "data" / "raw" / "detail" / "kleague_soccer_2023_2026.json"
GAMES = ROOT / "data" / "processed" / "games.csv"

_PRE = ("FC",)
_SUF = ("FC", "HD", "SK", "하나", "상무", "유나", "아이", "시티", "삼성", "현대", "스틸")


def _norm(n: str) -> str:
    """프로토와 네이버 표기 차이 — FC서울↔서울, 울산HD↔울산."""
    n = str(n).strip()
    for p in _PRE:
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    for s in _SUF:
        if n.endswith(s) and len(n) > len(s):
            n = n[: -len(s)]
            break
    return n


def reserves() -> pd.DataFrame:
    """경기별 각 팀의 **비주전 투입 인원**. 주전은 그 경기 직전까지로 판정한다."""
    raw = json.loads(LINEUP.read_text(encoding="utf-8"))
    games = sorted(raw.values(), key=lambda g: g["date"])
    starts: dict = defaultdict(int)          # (팀, 시즌, 선수) → 그때까지 선발 횟수
    n_games: dict = defaultdict(int)
    rows = []
    for g in games:
        d = g.get("data") or {}
        season = g["date"][:4]
        rec = {}
        for side in ("home", "away"):
            s = d.get(side) or {}
            ps = s.get("players") or []
            if len(ps) < 11:
                rec = {}
                break
            team = g[side]
            xi = [p["playerId"] for p in ps]
            # ⚠️ 이 경기 **이전까지** 의 선발 횟수 상위 11명을 주전으로 본다.
            #    시즌 전체로 정하면 미래 정보가 샌다.
            hist = sorted(((starts[(team, season, p)], p) for p in
                           {k[2] for k in starts if k[0] == team and k[1] == season}),
                          reverse=True)
            core = {p for _, p in hist[:11]}
            n_prior = n_games[(team, season)]
            rec[side] = {
                "team": team,
                "n_reserve": sum(1 for p in xi if p not in core) if n_prior >= 5 else None,
            }
        if rec and rec.get("home") and rec.get("away"):
            rows.append({
                "date": g["date"], "home": _norm(g["home"]), "away": _norm(g["away"]),
                "res_h": rec["home"]["n_reserve"], "res_a": rec["away"]["n_reserve"],
                "gf": g["home_score"], "ga": g["away_score"],
            })
        # 관측 후 갱신 (누수 방지)
        for side in ("home", "away"):
            s = d.get(side) or {}
            for p in (s.get("players") or []):
                starts[(g[side], season, p["playerId"])] += 1
            n_games[(g[side], season)] += 1
    df = pd.DataFrame(rows).dropna(subset=["res_h", "res_a"])
    df["res_diff"] = df["res_h"] - df["res_a"]     # +면 홈이 2군을 더 냈다
    return df


def with_odds(df: pd.DataFrame) -> pd.DataFrame:
    """프로토 승무패(3-way) 배당과 붙인다.

    🔴 **무승부를 빼면 안 된다.** 처음에 result 를 홈승/홈패로 걸렀더니
       "2군 낸 팀 반대편 ROI +14.37%" 가 나왔다. 그런데 **반대 방향도 +25.73%**,
       **무작위로 한쪽 사기도 +19.90%** 였다. 3-way 에서 홈을 샀는데 무승부면
       지는 건데 그 지는 경우를 통째로 뺐으니 양쪽 다 벌 수밖에 없다.
       세션 초반의 '마켓 정합성 가짜 +12.48%' 와 **같은 버그**다.
       무승부는 홈·원정 베팅 모두에게 **패배**로 들어가야 한다.
    """
    g = pd.read_csv(GAMES)
    g = g[(g["market_family"] == "승무패") & (g["n_way"] == 3) & (g["sport"] == "sc")
          & (~g["is_void"].astype(bool))
          & (g["result"].isin(["홈승", "무승부", "홈패"]))].copy()
    _H = re.compile(r"^(.+?)\s+(-?\d+)\s*$")
    _A = re.compile(r"^(-?\d+)\s+(.+?)\s*$")
    g["h"] = [_norm(_H.match(str(x)).group(1) if _H.match(str(x)) else x) for x in g["home"]]
    g["a"] = [_norm(_A.match(str(x)).group(2) if _A.match(str(x)) else x) for x in g["away"]]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g["key"] = [f"{y}-{m}-{d}|{h}|{a}" for y, m, d, h, a in
                zip(g["year"], md[0].fillna(""), md[1].fillna(""), g["h"], g["a"])]
    df = df.copy()
    df["key"] = [f"{d}|{h}|{a}" for d, h, a in zip(df["date"], df["home"], df["away"])]
    j = g.merge(df, on="key", how="inner").drop_duplicates("key")
    o = j["odds"].astype(str).str.split(",", expand=True)
    if o.shape[1] < 3:
        return j.iloc[0:0]
    j["o_home"] = o[0].astype(float)
    j["o_away"] = o[2].astype(float)
    j["home_win"] = (j["result"] == "홈승").astype(int)
    j["away_win"] = (j["result"] == "홈패").astype(int)   # 무승부는 둘 다 0 = 패배
    return j


def _boot(x: np.ndarray, n: int = 4000, seed: int = 42):
    rng = np.random.default_rng(seed)
    return np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)])


def run(j: pd.DataFrame, cut: int = 2) -> None:
    """2군을 더 많이 낸 팀의 **반대편**을 산다."""
    s = j[abs(j["res_diff"]) >= cut].copy()
    if len(s) < 40:
        print(f"  차이 {cut}명 이상: 표본 {len(s)} — 부족")
        return
    # res_diff > 0 이면 홈이 2군을 더 냈다 → 원정을 산다
    buy_home = s["res_diff"] < 0
    s["odds"] = np.where(buy_home, s["o_home"], s["o_away"])
    s["hit"] = np.where(buy_home, s["home_win"], s["away_win"])   # 무승부는 양쪽 다 패
    s["ret"] = np.where(s["hit"] == 1, s["odds"] - 1, -1.0)
    bs = _boot(s["ret"].values)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(f"  차이 {cut}명+ : n={len(s):>4}  적중 {s['hit'].mean()*100:5.2f}%  "
          f"ROI {s['ret'].mean()*100:+6.2f}%  95%CI [{lo*100:+.1f}, {hi*100:+.1f}]")
    yr = s.assign(y=s["date"].str[:4]).groupby("y")["ret"].agg(["size", "mean"])
    print("      연도별 " + " · ".join(
        f"{i} {r['mean']*100:+.1f}%(n={int(r['size'])})" for i, r in yr.iterrows()))
    if lo > 0:
        print("      ✅ CI 가 0 위 — 마진 12% 와 비교할 것")
    else:
        print("      🔴 CI 가 0 을 포함 — 기각")


def _selftest() -> int:
    d = reserves()
    bad = []
    print("라인업 우위 자기검사")
    print(f"  라인업 경기 {len(d):,}")
    if not d.empty and not ((d["res_h"] >= 0) & (d["res_h"] <= 11)).all():
        bad.append("비주전 인원이 0~11 범위 밖")
    print("  ✅ 비주전 인원 범위")
    j = with_odds(d)
    print(f"  프로토 결합 {len(j):,}")
    if len(j) < 50:
        bad.append(f"결합 {len(j)}건 — 이름 정규화 실패 의심")
    print("  ✅ 결합 규모")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 통과")
    return 0


def main() -> int:
    d = reserves()
    j = with_odds(d)
    print(f"K리그1 라인업 {len(d):,}경기 · 프로토 승무패 결합 {len(j):,}경기\n")
    print("가설: 2군을 더 많이 낸 팀의 반대편을 사면 이긴다")
    print("  (라인업은 킥오프 1시간 전 공개 · 프로토 마감은 10분 전 → 알고 살 수 있다)\n")
    for cut in (1, 2, 3, 4):
        run(j, cut)
    print("\n⚠️ 프로토 3-way 마진은 13%다. CI 하단이 0 을 넘어도 그걸 못 넘으면 실전 불가다.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
