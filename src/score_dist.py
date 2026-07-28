"""스코어 분포 모델 — 하나의 예측으로 **모든 마켓**을 가격 매긴다.

왜 필요한가
------------
지금까지 승패·승무패만 봤다. 그런데 프로토 물량은 이렇다:

    언더오버   26.1%   ← 최대
    승패       24.4%   ← 우리가 본 것
    핸디캡2way 15.3%
    핸디캡3way 14.0%
    승무패     13.8%   ← 우리가 본 것
    승①패      4.9%

**61.2% 를 안 보고 있었다.** 야구만 봐도 승패는 32.6% 뿐이다.

더 큰 문제는 구조다. 승패 확률만 있으면 **"근소 우위"를 표현할 방법이 없다.**
"삼성이 조금 낫다"는 판단은 승패 베팅이 아니라
핸디캡(-1.5는 무리, +1.5는 안전)이나 언더(접전이면 낮은 스코어)로 풀어야 할 수도 있다.

해결: **스코어 분포를 예측하고 거기서 모든 마켓을 유도한다.**

    P(홈=i, 원정=j)
      ├→ 승패      P(i > j)
      ├→ 승무패    P(i>j), P(i=j), P(i<j)
      ├→ 언더오버   P(i+j > line)
      ├→ 핸디캡    P(i−j > handicap)
      └→ 승①패     P(i−j≥2), P(|i−j|=1), P(j−i≥2)

모델
----
독립 포아송 + **Dixon-Coles 저득점 보정**.
λ 는 walk-forward 로 추정한다(그 경기 이전 기록만):

    λ_home = (홈팀 최근 평균득점 + 원정팀 최근 평균실점) / 2 × 홈보정
    λ_away = (원정팀 최근 평균득점 + 홈팀 최근 평균실점) / 2

⚠️ 채택은 측정 후. 마켓별로 **프로토 배당보다 정확한지**를 각각 검증한다.
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque
from math import erf, exp, lgamma, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matches import load_matches                       # noqa: E402

TRAIN_END = 2024
WINDOW = 20                 # 팀별 최근 경기 창
MAX_GOALS = {"bs": 20, "sc": 8, "bk": 160, "vl": 6}   # 종목별 분포 상한
HOME_MULT = {"bs": 1.03, "sc": 1.12, "bk": 1.02, "vl": 1.05}


def _pois(lam: float, k: int) -> float:
    """포아송 pmf. **로그 공간**에서 계산한다.

    농구는 λ ≈ 105 라 lam**k 가 그대로는 오버플로한다(실제로 났다).
    """
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    lp = -lam + k * np.log(lam) - lgamma(k + 1)
    return float(np.exp(lp)) if lp > -700 else 0.0


def dixon_coles_tau(i: int, j: int, li: float, lj: float, rho: float) -> float:
    """저득점 구간(0-0,1-0,0-1,1-1) 보정. 독립 포아송은 이 구간을 못 맞춘다."""
    if i == 0 and j == 0:
        return 1 - li * lj * rho
    if i == 0 and j == 1:
        return 1 + li * rho
    if i == 1 and j == 0:
        return 1 + lj * rho
    if i == 1 and j == 1:
        return 1 - rho
    return 1.0


def joint(lh: float, la: float, sport: str, rho: float = 0.0) -> np.ndarray:
    """P(홈=i, 원정=j) 행렬.

    ⚠️ 농구처럼 λ 가 크면(≈105) 포아송 격자가 비효율적이고 분산도 실제와 안 맞는다.
       λ 가 30 을 넘으면 **정규근사**로 평균 주변만 격자를 만든다.
    """
    if max(lh, la) > 30:
        return _joint_normal(lh, la)
    n = MAX_GOALS.get(sport, 12) + 1
    ph = np.array([_pois(lh, i) for i in range(n)])
    pa = np.array([_pois(la, j) for j in range(n)])
    M = np.outer(ph, pa)
    if rho and sport == "sc":
        for i in range(min(2, n)):
            for j in range(min(2, n)):
                M[i, j] *= dixon_coles_tau(i, j, lh, la, rho)
    s = M.sum()
    return M / s if s > 0 else M


# ---------------------------------------------------------------- 마켓 확률

def _joint_normal(lh: float, la: float, span: int = 4) -> np.ndarray:
    """득점이 큰 종목(농구)용. 평균 ± span·σ 범위만 이산 격자로 만든다.

    반환 행렬의 인덱스는 실제 득점이 아니라 **오프셋**이며,
    아래 마켓 함수들이 인덱스 차·합만 쓰므로 중심을 맞춰 두면 된다.
    """
    sh, sa = sqrt(lh), sqrt(la)
    lo_h, hi_h = int(lh - span * sh), int(lh + span * sh) + 1
    lo_a, hi_a = int(la - span * sa), int(la + span * sa) + 1
    lo = min(lo_h, lo_a)
    hi = max(hi_h, hi_a)
    n = hi - lo + 1

    def npdf(x, mu, sd):
        return np.exp(-0.5 * ((x - mu) / sd) ** 2) / (sd * sqrt(2 * np.pi))

    idx = np.arange(lo, hi + 1)
    ph = npdf(idx, lh, sh)
    pa = npdf(idx, la, sa)
    M = np.outer(ph / ph.sum(), pa / pa.sum())
    # 인덱스를 실제 득점으로 되돌리기 위해 오프셋을 붙여 둔다
    M = np.pad(M, ((lo, 0), (lo, 0))) if lo > 0 else M
    return M


def p_win(M: np.ndarray) -> tuple[float, float, float]:
    """(홈승, 무, 원정승)"""
    n = M.shape[0]
    d = float(np.trace(M))
    h = float(sum(M[i, j] for i in range(n) for j in range(i)))
    return h, d, 1.0 - h - d


def p_over(M: np.ndarray, line: float) -> float:
    """P(총합 > line)"""
    n = M.shape[0]
    return float(sum(M[i, j] for i in range(n) for j in range(n) if i + j > line))


def p_handicap(M: np.ndarray, h: float) -> tuple[float, float, float]:
    """홈에 핸디 h 를 얹었을 때 (핸디승, 핸디무, 핸디패).
    h = −1.5 면 홈이 2점차 이상 이겨야 승."""
    n = M.shape[0]
    win = draw = 0.0
    for i in range(n):
        for j in range(n):
            d = (i + h) - j
            if d > 1e-9:
                win += M[i, j]
            elif abs(d) < 1e-9:
                draw += M[i, j]
    return float(win), float(draw), float(1 - win - draw)


def p_margin_band(M: np.ndarray, band: int) -> tuple[float, float, float]:
    """마진 밴드형 3-way — (홈 band+1점차 이상 승, |차| ≤ band, 원정 band+1점차 이상 승).

    band=1 → 승①패(야구)   band=5 → 승⑤패(농구·배구)
    """
    n = M.shape[0]
    a = float(sum(M[i, j] for i in range(n) for j in range(n) if i - j > band))
    m = float(sum(M[i, j] for i in range(n) for j in range(n) if abs(i - j) <= band))
    return a, m, float(max(0.0, 1 - a - m))


def p_one_run(M: np.ndarray) -> tuple[float, float, float]:
    """승①패 — (홈 2점차+승, 1점차 이내, 원정 2점차+승)"""
    return p_margin_band(M, 1)


# ---------------------------------------------------------------- λ 추정

def build_lambdas(m: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 각 경기의 λ 는 **그 경기 이전** 기록만 쓴다."""
    gf: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    ga: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []
    for r in m.itertuples():
        kh, ka = (r.league, r.home_team), (r.league, r.away_team)
        ok = len(gf[kh]) >= 8 and len(gf[ka]) >= 8
        if ok:
            hm = HOME_MULT.get(r.sport, 1.05)
            lh = (np.mean(gf[kh]) + np.mean(ga[ka])) / 2 * hm
            la = (np.mean(gf[ka]) + np.mean(ga[kh])) / 2
        else:
            lh = la = np.nan
        rows.append({"date": r.date, "year": r.year, "league": r.league,
                     "sport": r.sport, "home_team": r.home_team,
                     "away_team": r.away_team,
                     "home_score": r.home_score, "away_score": r.away_score,
                     "lam_home": lh, "lam_away": la})
        gf[kh].append(r.home_score); ga[kh].append(r.away_score)
        gf[ka].append(r.away_score); ga[ka].append(r.home_score)
    return pd.DataFrame(rows)


def main() -> int:
    m = load_matches()
    df = build_lambdas(m)
    ok = df.dropna(subset=["lam_home", "lam_away"])
    print(f"경기 {len(df):,} · λ 추정 가능 {len(ok):,}")

    # 종목별 λ 적합도 — 예측 총득점 vs 실제
    print(f"\n{'종목':<6}{'n':>8}{'예측 총득점':>12}{'실제 총득점':>12}{'편차':>9}")
    print("-" * 50)
    for sp, name in (("bs", "야구"), ("sc", "축구"), ("bk", "농구"), ("vl", "배구")):
        s = ok[ok["sport"] == sp]
        if len(s) < 300:
            continue
        pred = (s["lam_home"] + s["lam_away"]).mean()
        act = (s["home_score"] + s["away_score"]).mean()
        print(f"{name:<6}{len(s):>8,}{pred:>12.2f}{act:>12.2f}{pred-act:>+9.2f}")

    ok.to_csv(Path(__file__).resolve().parent.parent / "data" / "processed"
              / "lambdas.csv", index=False)
    print("\n저장: data/processed/lambdas.csv")

    # 유도 확률 예시
    print("\n=== 유도 예시 (KBO 최근 경기) ===")
    s = ok[(ok["league"] == "KBO")].tail(1)
    if len(s):
        r = s.iloc[0]
        M = joint(r["lam_home"], r["lam_away"], "bs")
        h, d, a = p_win(M)
        print(f"  {r['home_team']} vs {r['away_team']}  "
              f"λ={r['lam_home']:.2f}/{r['lam_away']:.2f}")
        print(f"    승패        홈 {h/(h+a):.1%} / 원정 {a/(h+a):.1%}")
        for line in (7.5, 8.5, 9.5, 10.5):
            print(f"    U/O {line}    오버 {p_over(M, line):.1%}")
        for hc in (-1.5, -0.5, 0.5, 1.5):
            w, dr, l = p_handicap(M, hc)
            print(f"    핸디 {hc:+.1f}  승 {w:.1%} / 무 {dr:.1%} / 패 {l:.1%}")
        a2, m2, l2 = p_one_run(M)
        print(f"    승①패      홈2+ {a2:.1%} / 1점차 {m2:.1%} / 원정2+ {l2:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
