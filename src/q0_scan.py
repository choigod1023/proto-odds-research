"""Q0 — 과거 데이터에 +EV 구간이 존재하는가.

핵심 아이디어
--------------
배당이 완벽히 정확하다면, **어느 선택지에 걸든 수익률은 정확히 같다.**

    적중확률 p_i = (1/o_i) / 오버라운드   (마진 균등 배분 가정)
    수익률   = p_i · o_i − 1 = 1/오버라운드 − 1

즉 이론 수익률은 booking 구조만으로 정해진다:

    2-way        1/1.1364 − 1 = −12.00%
    3-way        1/1.1494 − 1 = −13.00%
    3-way 핸디캡  1/1.1629 − 1 = −14.01%

**이 기준선에서 벗어나는 구간이 곧 시장이 틀린 지점이다.**

⚠️ 다중검정 주의: 구간을 많이 쪼갤수록 우연히 플러스가 나오는 조합이 생긴다.
   점추정이 아니라 **부트스트랩 신뢰구간**으로 판정하고, 검정 구간 수를 함께 보고한다.

사용:
    python src/build_dataset.py   # 선행 1회
    python src/q0_scan.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_BOOT = 5000
MIN_N = 300

DATA = Path(__file__).resolve().parent.parent / "data" / "processed" / "bets.csv"

THEORETICAL = {"2-way": 1 / 1.1364 - 1,
               "3-way": 1 / 1.1494 - 1,
               "3-way-핸디캡": 1 / 1.1629 - 1}

BUCKETS = [(1.0, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]


def load() -> pd.DataFrame:
    if not DATA.exists():
        print(f"{DATA} 가 없습니다. 먼저 python src/build_dataset.py 를 실행하세요.")
        raise SystemExit(1)
    df = pd.read_csv(DATA)
    df["theo"] = df["booking_class"].map(THEORETICAL).fillna(-0.12)
    df["bucket"] = pd.cut(df["odds"],
                          bins=[b[0] for b in BUCKETS] + [999],
                          labels=[f"{a:.1f}–{b:.1f}" if b < 999 else "5.0+"
                                  for a, b in BUCKETS],
                          right=False)
    # 게임행 단위 클러스터 id (같은 행의 선택지들은 서로 독립이 아니다)
    df["cluster"] = (df["year"].astype(str) + "-" + df["round"].astype(str)
                     + "-" + df["game_no"].astype(str))
    print(f"베팅 레코드 {len(df):,}건 · 회차 {df.groupby(['year','round']).ngroups}개 "
          f"· 연도 {sorted(df['year'].unique())}")
    return df


def cluster_bootstrap(sub: pd.DataFrame, n_boot: int = N_BOOT,
                      alpha: float = 0.05) -> tuple[float, float]:
    """게임행 단위 클러스터 부트스트랩 (numpy 벡터화).

    같은 게임행의 선택지는 '정확히 하나만 적중'하도록 묶여 있어 독립이 아니다.
    베팅 단위로 재추출하면 신뢰구간이 실제보다 좁아져 없는 패턴을 있다고 착각하게 된다.
    """
    codes, _ = pd.factorize(sub["cluster"])
    k = codes.max() + 1
    if k < 30:
        return (float("nan"), float("nan"))

    prof = sub["profit"].to_numpy(dtype=float)
    sums = np.bincount(codes, weights=prof, minlength=k)
    cnts = np.bincount(codes, minlength=k).astype(float)

    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, k, size=(n_boot, k))
    tot = sums[idx].sum(axis=1)
    num = cnts[idx].sum(axis=1)
    rois = tot / num
    return (float(np.quantile(rois, alpha / 2)),
            float(np.quantile(rois, 1 - alpha / 2)))


def baseline(df: pd.DataFrame) -> None:
    print("\n" + "=" * 82)
    print("1) 기준선 검증 — 매핑이 맞다면 실측 ROI가 이론값 근처여야 한다")
    print("=" * 82)
    print(f"{'booking 구조':<16}{'n':>10}{'이론 ROI':>11}{'실측 ROI':>11}{'차이':>10}  판정")
    for cls, g in df.groupby("booking_class", sort=False):
        th = THEORETICAL.get(cls, float("nan"))
        ac = g["profit"].mean()
        d = ac - th
        print(f"{cls:<16}{len(g):>10,}{th:>10.2%}{ac:>11.2%}{d:>+10.2%}  "
              f"{'✅ 정상' if abs(d) < 0.03 else '⚠️ 이탈'}")


def scan(df: pd.DataFrame, cols, title: str, min_n: int = MIN_N) -> int:
    g = df.groupby(cols, observed=True)
    agg = g.agg(n=("profit", "size"), roi=("profit", "mean"),
                theo=("theo", "mean")).reset_index()
    agg = agg[agg["n"] >= min_n].copy()
    agg["excess"] = agg["roi"] - agg["theo"]
    agg = agg.sort_values("roi", ascending=False)

    print("\n" + "-" * 82)
    print(f"■ {title}   (표본 {min_n}건 이상)")
    print("-" * 82)
    print(f"{'구간':<34}{'n':>9}{'실측 ROI':>11}{'기준선':>10}{'초과':>10}")
    for _, r in agg.iterrows():
        key = " / ".join(str(r[c]) for c in (cols if isinstance(cols, list) else [cols]))
        flag = "  ⭐" if r["roi"] > 0 else ("  ↑" if r["excess"] > 0.03 else "")
        print(f"{key:<34}{int(r['n']):>9,}{r['roi']:>11.2%}"
              f"{r['theo']:>10.2%}{r['excess']:>+10.2%}{flag}")
    return len(agg)


def main() -> int:
    df = load()
    baseline(df)

    print("\n" + "=" * 82)
    print("2) 구간 스캔 — 기준선을 넘는 조합이 있는가")
    print("=" * 82)
    n_tests = 0
    n_tests += scan(df, "bucket", "배당 구간별")
    n_tests += scan(df, ["sport", "market_family"], "종목 × 상품")
    n_tests += scan(df, ["market_family", "selection"], "상품 × 선택지")
    n_tests += scan(df, "league", "리그별", min_n=1000)
    n_tests += scan(df, "year", "연도별")
    n_tests += scan(df, ["market_family", "selection", "bucket"],
                    "상품 × 선택지 × 배당구간", min_n=500)

    # ---- 플러스 구간에 부트스트랩 CI
    print("\n" + "=" * 82)
    print("3) 플러스 후보 구간의 부트스트랩 95% 신뢰구간 (게임행 클러스터, 시드 42)")
    print("=" * 82)
    g = df.groupby(["market_family", "selection", "bucket"], observed=True)
    cands = []
    for key, sub in g:
        if len(sub) >= 500 and sub["profit"].mean() > 0:
            cands.append((key, sub))
    if not cands:
        print("  ROI가 플러스인 구간이 없습니다.")
    for key, sub in sorted(cands, key=lambda x: -x[1]["profit"].mean())[:12]:
        lo, hi = cluster_bootstrap(sub)
        v = "✅ 유의 (하한>0)" if lo > 0 else "❌ 0을 포함 → 우연과 구분 안 됨"
        print(f"  {'/'.join(map(str,key)):<40} n={len(sub):>7,}  "
              f"ROI={sub['profit'].mean():+.2%}  95%CI=[{lo:+.2%}, {hi:+.2%}]  {v}")

    print(f"\n※ 검정한 구간 수 약 {n_tests}개. 다중검정 때문에 우연히 플러스가 나오는 "
          f"구간이 생긴다.\n  신뢰구간 하한이 0을 넘는 것만 후보로 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
