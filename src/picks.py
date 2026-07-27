"""픽 엔진 — 모델 확률과 시장 배당을 비교해 베팅 대상을 고른다.

    EV = 모델확률 × 배당 − 1

EV > 임계값인 선택지만 고른다. **전 경기 베팅은 하지 않는다.**

⚠️ 반드시 백테스트로 검증한다.
   "모델이 좋다고 한 픽"이 실제로 −12% 기준선보다 나았는지 확인하지 않으면
   그 픽은 근거 없는 숫자일 뿐이다. 이 스크립트는 픽 규칙을 과거에 그대로 적용해
   실제 수익률과 부트스트랩 신뢰구간을 함께 낸다.

사용:
    python src/picks.py            # 백테스트
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elo_model import fit_logistic, load_results, prob_home, run_elo   # noqa: E402

SEED = 42
N_BOOT = 5000
TRAIN_END = 2024          # 2023~2024 학습 → 2025~2026 검증 (시간 분리)
EV_GRID = [0.0, 0.02, 0.05, 0.08, 0.12]


def build() -> tuple[pd.DataFrame, tuple[float, float], dict]:
    g = load_results()
    g, ratings = run_elo(g)
    train = g[g["year"] <= TRAIN_END]
    a, b = fit_logistic(train)
    g["p_home"] = prob_home(g["elo_diff_pre"].to_numpy(), a, b)
    print(f"경기 {len(g):,}건 · 리그 {g['league'].nunique()}개 "
          f"· 학습 {len(train):,} / 검증 {len(g)-len(train):,}")
    print(f"로지스틱 계수  a={a:+.4f}  b={b:+.6f}  "
          f"(Elo 100차 → 홈승률 {prob_home(100,a,b):.1%})")
    return g, (a, b), ratings


def attach_odds(g: pd.DataFrame) -> pd.DataFrame:
    """승패(2-way) 시장의 배당을 경기에 붙인다. 2-way가 환급률이 가장 좋다.

    결합 키는 **(리그, 홈팀, 원정팀, 날짜)** 다.
    회차 번호로 붙이면 안 된다 — 같은 경기가 여러 회차에 중복 발매된다(matches.py 참조).
    """
    from matches import GAMES, _DATE_RE, _away, _home
    raw = pd.read_csv(GAMES)
    raw = raw[(~raw["is_void"].astype(bool)) & (raw["market_family"] == "승패")
              & (raw["n_way"] == 2) & (raw["result"].isin(["홈승", "홈패"]))]
    parts = raw["odds"].str.split(",", expand=True)
    raw = raw.assign(o_home=pd.to_numeric(parts[0], errors="coerce"),
                     o_away=pd.to_numeric(parts[1], errors="coerce"))

    hs = raw["home"].map(_home)
    aw = raw["away"].map(_away)
    raw = raw.assign(home_team=[t for t, _ in hs], away_team=[t for _, t in aw])
    md = raw["date_text"].astype(str).str.extract(_DATE_RE)
    raw = raw.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                     _dd=pd.to_numeric(md[1], errors="coerce"))
    raw = raw.dropna(subset=["home_team", "away_team", "_mm", "_dd",
                             "o_home", "o_away"])
    raw["date"] = pd.to_datetime(
        dict(year=raw["year"], month=raw["_mm"].astype(int),
             day=raw["_dd"].astype(int)), errors="coerce")
    raw = raw.dropna(subset=["date"])

    key = ["league", "home_team", "away_team", "date"]
    raw = raw.drop_duplicates(key)
    m = g.merge(raw[key + ["o_home", "o_away"]], on=key, how="inner")
    print(f"배당 결합 후 {len(m):,}경기 (승패 2-way)")
    return m


def backtest(m: pd.DataFrame) -> None:
    """픽 규칙을 과거에 적용했을 때 실제로 기준선을 넘었는가."""
    test = m[m["year"] > TRAIN_END].copy()
    test = test[test["outcome"] != 0.5]        # 승패 시장엔 무승부가 없다
    test["p_away"] = 1 - test["p_home"]
    test["ev_home"] = test["p_home"] * test["o_home"] - 1
    test["ev_away"] = test["p_away"] * test["o_away"] - 1

    print("\n" + "=" * 78)
    print(f"백테스트 — 학습 ~{TRAIN_END} / 검증 {TRAIN_END+1}~ (시간 분리)")
    print("=" * 78)
    print("기준선: 2-way 시장에 아무거나 걸면 −12.00%\n")
    print(f"{'EV 임계':>8}{'픽 수':>9}{'적중률':>9}{'실제 ROI':>11}"
          f"{'95% 신뢰구간':>26}  판정")

    rng = np.random.default_rng(SEED)
    for th in EV_GRID:
        rows = []
        for r in test.itertuples():
            if r.ev_home > th and r.ev_home >= r.ev_away:
                rows.append((r.o_home, r.outcome == 1.0))
            elif r.ev_away > th:
                rows.append((r.o_away, r.outcome == 0.0))
        if len(rows) < 50:
            print(f"{th:>8.0%}{len(rows):>9,}  표본 부족")
            continue
        odds = np.array([o for o, _ in rows])
        won = np.array([w for _, w in rows])
        profit = np.where(won, odds - 1, -1.0)
        roi = profit.mean()
        idx = rng.integers(0, len(profit), size=(N_BOOT, len(profit)))
        dist = profit[idx].mean(axis=1)
        lo, hi = np.quantile(dist, [0.025, 0.975])
        v = ("✅ 유의한 +EV" if lo > 0 else
             ("△ 기준선 상회" if roi > -0.12 else "❌ 기준선 미달"))
        print(f"{th:>8.0%}{len(rows):>9,}{won.mean():>9.1%}{roi:>11.2%}"
              f"{f'[{lo:+.2%}, {hi:+.2%}]':>26}  {v}")

    # 모델 자체의 품질 — 캘리브레이션
    print("\n모델 캘리브레이션 (검증 구간, 홈 기준)")
    print(f"  {'예측확률 구간':<16}{'n':>7}{'예측평균':>10}{'실제승률':>10}{'차이':>9}")
    test["bin"] = pd.cut(test["p_home"], np.arange(0, 1.01, 0.1))
    for b, s in test.groupby("bin", observed=True):
        if len(s) < 50:
            continue
        pm, am = s["p_home"].mean(), (s["outcome"] == 1.0).mean()
        print(f"  {str(b):<16}{len(s):>7,}{pm:>10.1%}{am:>10.1%}{am-pm:>+9.1%}")

    # 시장과 비교 — 이게 핵심이다
    print("\n모델 vs 시장 (검증 구간, Brier score · 낮을수록 정확)")
    ov = 1 / test["o_home"] + 1 / test["o_away"]
    p_mkt = (1 / test["o_home"]) / ov
    y = (test["outcome"] == 1.0).astype(float)
    bm = float(((test["p_home"] - y) ** 2).mean())
    bk = float(((p_mkt - y) ** 2).mean())
    print(f"  모델 Brier {bm:.4f}   시장 Brier {bk:.4f}   "
          f"{'✅ 모델 우위' if bm < bk else '❌ 시장 우위'}")
    print("  ※ 시장보다 Brier가 낮지 않으면 +EV 픽은 나올 수 없다.")


def main() -> int:
    g, coef, ratings = build()
    m = attach_odds(g)
    backtest(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
