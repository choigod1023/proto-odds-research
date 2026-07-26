"""Q1 — 프로토의 마진(booking)은 상수인가.

초판 §1은 **2025년 99회차 단 하나**를 근거로 "유형별 목표 환급률을 기계적으로 적용한다"고
결론지었다. 회차 *내부* 편차가 0.0007이라는 것은 회차 안에서 균일하다는 뜻일 뿐,
회차 간·연도 간에도 같은 값인지는 측정된 적이 없다.

이 값은 EV 계산의 분모다. 회차마다 다르면 +EV/−EV 판정이 통째로 흔들린다.

판정 기준은 DESIGN.md 에 **측정 전에** 확정해 두었다:
    Q1-a 회차 간 상수      회차별 평균 booking의 SD < 0.002
    Q1-b 연도 간 변동      연도별 평균 차이 ≥ 0.005 → 연도별 booking 필요
    Q1-c 2026-03-27 개편   개편 전후 차이 ≥ 0.005
    Q1-d 종목별 차이       종목 간 최대 차이 ≥ 0.005
    Q1-e 야구단독 vs 혼합   차이 ≥ 0.005
    Q1-f 배당수준 의존     회귀 기울기 p<0.01 AND 예측범위 폭 > 0.003
                          → 균등 마진 가정 기각 (power/shin devig 필수)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"

SD_CONST = 0.002      # Q1-a 임계
DIFF_TH = 0.005       # Q1-b~e 임계
SLOPE_RANGE_TH = 0.003  # Q1-f 임계
SPORTS = {"sc": "축구", "bs": "야구", "bk": "농구", "vl": "배구"}


def load() -> pd.DataFrame:
    g = pd.read_csv(GAMES)
    g = g[(~g["is_void"].astype(bool)) & g["overround"].notna()]
    g = g[(g["overround"] >= 1.0) & (g["overround"] <= 1.40)]
    print(f"게임행 {len(g):,}건 · 회차 {g.groupby(['year','round']).ngroups}개 "
          f"· {sorted(g['year'].unique())}")
    return g


def hdr(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def q1a(g: pd.DataFrame) -> None:
    hdr("Q1-a  booking이 회차 간 상수인가   [통과 조건: 회차별 평균의 SD < 0.002]")
    print(f"{'booking 구조':<16}{'회차수':>7}{'게임행':>9}{'전체평균':>11}"
          f"{'회차별 SD':>11}{'최소':>10}{'최대':>10}  판정")
    for cls, sub in g.groupby("booking_class"):
        per = sub.groupby(["year", "round"])["overround"].mean()
        sd = per.std()
        ok = sd < SD_CONST
        print(f"{cls:<16}{len(per):>7}{len(sub):>9,}{sub['overround'].mean():>11.4f}"
              f"{sd:>11.5f}{per.min():>10.4f}{per.max():>10.4f}  "
              f"{'✅ 상수' if ok else '❌ 변동'}")


def q1b(g: pd.DataFrame) -> None:
    hdr("Q1-b  연도 간 변하는가   [변동 조건: 연도별 차이 ≥ 0.005]")
    t = g.pivot_table(index="booking_class", columns="year",
                      values="overround", aggfunc="mean")
    print(t.round(4).to_string())
    print()
    for cls, row in t.iterrows():
        rng = row.max() - row.min()
        print(f"  {cls:<16} 연도간 최대차 {rng:.5f}  "
              f"{'❌ 시기별 변동 있음' if rng >= DIFF_TH else '✅ 연도 무관'}")


def q1c(g: pd.DataFrame) -> None:
    hdr("Q1-c  2026-03-27 운영방식 개편으로 바뀌었는가   [기준: 차이 ≥ 0.005]")
    # 2026년은 개편 후, 2023~2025는 개편 전
    g = g.copy()
    g["period"] = np.where(g["year"] >= 2026, "개편후(2026)", "개편전(2023~25)")
    t = g.pivot_table(index="booking_class", columns="period",
                      values="overround", aggfunc="mean")
    t["차이"] = (t.get("개편후(2026)", np.nan) - t.get("개편전(2023~25)", np.nan))
    print(t.round(5).to_string())
    print()
    for cls, row in t.iterrows():
        d = abs(row["차이"])
        print(f"  {cls:<16} |차이|={d:.5f}  "
              f"{'❌ 개편으로 변경됨' if d >= DIFF_TH else '✅ 개편 영향 없음'}")


def q1d(g: pd.DataFrame) -> None:
    hdr("Q1-d  종목별로 다른가   [기준: 종목 간 최대차 ≥ 0.005]")
    t = g.pivot_table(index="booking_class", columns="sport",
                      values="overround", aggfunc="mean")
    t.columns = [SPORTS.get(c, c) for c in t.columns]
    print(t.round(4).to_string())
    print()
    for cls, row in t.iterrows():
        r = row.dropna()
        if len(r) < 2:
            continue
        rng = r.max() - r.min()
        print(f"  {cls:<16} 종목간 최대차 {rng:.5f}  "
              f"{'❌ 종목별 분리 필요' if rng >= DIFF_TH else '✅ 종목 무관'}")


def q1e(g: pd.DataFrame) -> None:
    hdr("Q1-e  야구 단독 회차 vs 혼합 회차   [기준: 차이 ≥ 0.005]")
    comp = g.groupby(["year", "round"])["sport"].apply(
        lambda s: "야구단독" if set(s.dropna()) <= {"bs"} else "혼합")
    g = g.join(comp.rename("round_type"), on=["year", "round"])
    n_solo = (comp == "야구단독").sum()
    print(f"  야구 단독 회차 {n_solo}개 / 전체 {len(comp)}개")
    if n_solo == 0:
        print("  → 야구 단독 회차가 없어 비교 불가 (모든 회차가 혼합)")
        return
    t = g.pivot_table(index="booking_class", columns="round_type",
                      values="overround", aggfunc="mean")
    print(t.round(5).to_string())


def q1f(g: pd.DataFrame) -> None:
    hdr("Q1-f ⭐ 배당 수준에 따라 마진 배분이 다른가   "
        "[기각 조건: p<0.01 AND 예측범위 폭 > 0.003]")
    print("  강팀 내재확률(1/최저배당)이 커질수록 오버라운드가 달라지는가를 회귀한다.")
    print("  균등 마진 가정이 깨지면 multiplicative devig는 구조적으로 틀린 방법이 된다.\n")

    g = g.copy()
    g["fav"] = g["odds"].str.split(",").apply(
        lambda xs: 1.0 / min(float(x) for x in xs if x))

    print(f"{'booking 구조':<16}{'n':>9}{'기울기 β':>12}{'p값':>12}"
          f"{'예측범위 폭':>13}  판정")
    for cls, sub in g.groupby("booking_class"):
        x, y = sub["fav"].to_numpy(), sub["overround"].to_numpy()
        res = stats.linregress(x, y)
        # 실제 관측된 fav 범위(1~99분위)에서 예측값이 얼마나 움직이는가
        lo, hi = np.quantile(x, [0.01, 0.99])
        width = abs(res.slope) * (hi - lo)
        reject = (res.pvalue < 0.01) and (width > SLOPE_RANGE_TH)
        print(f"{cls:<16}{len(sub):>9,}{res.slope:>12.5f}{res.pvalue:>12.2e}"
              f"{width:>13.5f}  {'❌ 균등마진 기각' if reject else '✅ 균등마진 유지'}")

    # 구간별로 직접 보여주기 — 회귀보다 직관적
    print("\n  강팀 내재확률 구간별 평균 오버라운드:")
    g["fav_bin"] = pd.cut(g["fav"], [0, .4, .5, .6, .7, .8, 1.0])
    t = g.pivot_table(index="fav_bin", columns="booking_class",
                      values="overround", aggfunc=["mean", "size"],
                      observed=True)
    print(t.round(4).to_string())


def main() -> int:
    if not GAMES.exists():
        print("먼저 python src/build_dataset.py 를 실행하세요.")
        return 1
    g = load()
    q1a(g)
    q1b(g)
    q1c(g)
    q1d(g)
    q1e(g)
    q1f(g)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
