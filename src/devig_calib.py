"""Q4a — devig 방법별 캘리브레이션. 역배 확률이 과대평가되고 있나.

왜 이걸 재나
------------
사이트가 쓰는 확률은 generate_v2 의 `p_mkt = (1/o)/ov`, 즉 **multiplicative** 다.
마진이 모든 선택지에 비례 배분됐다는 가정인데, 이 가정이 틀리는 대표적인 자리가
**역배**다(favourite-longshot bias). 마진이 역배 쪽에 더 얹혀 있으면 multiplicative 는
역배 확률을 실제보다 높게 복원하고, 그러면 화면의 '적중 확률'이 역배에서 부풀려진다.

이 프로젝트의 배당대 ROI 표(1.0-1.3 −9.23% … 5.0+ −33.49%)는 그 편향이 있다는
간접 증거다. 여기서는 **직접** 잰다: devig 로 복원한 확률 대비 실제 적중률.

무엇을 답하나
-------------
1. multiplicative 가 역배를 과대평가하나 (예측 > 실제)
2. power / shin 이 더 잘 맞나 — 맞다면 k, z 는 얼마인가
3. 그래서 화면 확률을 바꿔야 하나

    python src/devig_calib.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import multiplicative, power, shin              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BETS = ROOT / "data" / "processed" / "bets.csv"

# 배당대 구간 — loss_filter 가 쓰는 것과 같은 경계로 맞춘다(비교 가능하게).
BINS = [1.0, 1.3, 1.5, 1.8, 2.2, 3.0, 5.0, 999]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]

METHODS = {"multiplicative": multiplicative, "power": power, "shin": shin}


def load() -> pd.DataFrame:
    b = pd.read_csv(BETS)
    # 정산된 것만. won 이 0/1 이어야 적중률을 잴 수 있다.
    b = b[b["won"].isin([0, 1])]
    # 마켓 하나(=한 게임행)의 선택지가 모두 있어야 devig 이 성립한다.
    b = b.dropna(subset=["odds", "overround", "n_way"])
    b = b[b["odds"] > 1.0]
    return b


def devigged(b: pd.DataFrame) -> pd.DataFrame:
    """마켓 단위로 묶어 방법별 확률을 붙인다."""
    out = []
    key = ["year", "round", "game_no", "market_family", "n_way"]
    for _, grp in b.groupby(key, sort=False):
        grp = grp.sort_values("sel_index")
        odds = grp["odds"].tolist()
        n = int(grp["n_way"].iloc[0])
        # 선택지 수가 n_way 와 다르면 마켓이 온전치 않다 — devig 이 무의미하다.
        if len(odds) != n or n < 2:
            continue
        row = grp.copy()
        for name, fn in METHODS.items():
            try:
                row[name] = fn(odds)
            except Exception:                              # noqa: BLE001
                row[name] = np.nan
        out.append(row)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def report(d: pd.DataFrame) -> None:
    d = d.copy()
    d["bin"] = pd.cut(d["odds"], BINS, labels=LABELS, right=False)

    print(f"표본 {len(d):,}건 · 마켓 {d.groupby(['year','round','game_no']).ngroups:,}개\n")

    # ── 1. 배당대별 예측 vs 실제
    print("배당대별 — 예측확률(복원) vs 실제적중률   (양수 = 과대평가)")
    head = f"{'배당대':<10}{'n':>8}{'실제':>8}"
    for m in METHODS:
        head += f"{m[:4]:>9}{'차이':>8}"
    print(head)
    print("-" * len(head))
    for lab in LABELS:
        s = d[d["bin"] == lab]
        if len(s) < 200:
            continue
        act = s["won"].mean()
        line = f"{lab:<10}{len(s):>8,}{act*100:>7.1f}%"
        for m in METHODS:
            pred = s[m].mean()
            line += f"{pred*100:>8.1f}%{(pred-act)*100:>+7.1f}"
        print(line)

    # ── 2. 전체 정확도 (Brier — 낮을수록 좋다)
    print("\n전체 Brier (낮을수록 정확)")
    for m in METHODS:
        s = d.dropna(subset=[m])
        br = float(((s[m] - s["won"]) ** 2).mean())
        print(f"  {m:<16} {br:.5f}   n={len(s):,}")

    # ── 3. 역배 구간만 따로 — 여기가 논점이다
    print("\n역배(배당 3.0 이상)만")
    s = d[d["odds"] >= 3.0]
    if len(s) >= 200:
        act = s["won"].mean()
        print(f"  n={len(s):,} · 실제 적중 {act*100:.2f}%")
        for m in METHODS:
            pred = s[m].mean()
            print(f"    {m:<16} 예측 {pred*100:5.2f}%  → {(pred-act)*100:+.2f}%p "
                  f"{'과대평가' if pred > act else '과소평가'}")
    else:
        print("  표본 부족")


def main() -> int:
    if not BETS.exists():
        print(f"bets.csv 가 없다: {BETS}")
        return 1
    b = load()
    print(f"정산 선택지 {len(b):,}건 → devig 계산 중...", flush=True)
    d = devigged(b)
    if d.empty:
        print("온전한 마켓이 없다")
        return 1
    report(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
