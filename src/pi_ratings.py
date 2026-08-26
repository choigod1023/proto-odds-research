"""점수차 기반 동적 레이팅 (pi-ratings 계열).

왜 만드는가
------------
`variable_impact.py` 측정에서 **세 종목 모두 '최근 득실 마진'이 '최근 승률'을 앞섰다.**
Elo는 승/무/패만 먹기 때문에 그 정보를 버린다. 같은 5승 5패라도
한 점 차로 이기고 크게 지는 팀과 그 반대는 실력이 다르다.

Constantinou & Fenton 의 pi-ratings 아이디어를 따른다:
    · 팀마다 **홈 레이팅과 원정 레이팅을 따로** 둔다
    · 예측 점수차와 실제 점수차의 오차로 갱신한다
    · 오차는 로그로 감쇠시켜 대량 득점 한 경기에 과민 반응하지 않게 한다
    · 홈에서 배운 것을 원정 레이팅에도 일부 전이시킨다(γ)

⚠️ 채택은 측정 후에. Elo보다 Brier가 낮지 않으면 쓰지 않는다.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matches import load_matches                        # noqa: E402
from variable_impact import _brier, _fit                # noqa: E402

TRAIN_END = 2024
SPORTS = {"bs": "야구", "sc": "축구", "bk": "농구", "vl": "배구"}

# 종목별 학습률 — 경기 수와 점수 스케일이 다르다
LAMBDA = {"bs": 0.06, "sc": 0.10, "bk": 0.035, "vl": 0.10}
GAMMA = 0.5          # 홈↔원정 전이 계수
DAMP = {"bs": 3.0, "sc": 1.2, "bk": 12.0, "vl": 1.5}   # 오차 감쇠 스케일


def _psi(e: float, c: float) -> float:
    """오차 감쇠. 큰 점수차 한 경기에 레이팅이 튀지 않게 로그로 누른다."""
    return float(np.sign(e) * np.log1p(abs(e) / c))


def run_pi(m: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 같은 날짜 결과도 그날 모든 예측을 만든 뒤 반영한다."""
    RH: dict = defaultdict(float)     # 홈 레이팅
    RA: dict = defaultdict(float)     # 원정 레이팅
    def apply_updates(pending) -> None:
        for kh, ka, lam, err in pending:
            # 홈팀: 홈 레이팅을 주로, 원정 레이팅에 일부 전이
            RH[kh] += lam * err
            RA[kh] += lam * GAMMA * err
            # 원정팀: 반대 방향
            RA[ka] -= lam * err
            RH[ka] -= lam * GAMMA * err

    rows = []
    pending = []
    current_date = None

    for r in m.itertuples():
        d = pd.Timestamp(r.date).normalize()
        if current_date is not None and d != current_date:
            apply_updates(pending)
            pending.clear()
        current_date = d
        kh, ka = (r.league, r.home_team), (r.league, r.away_team)
        lam = LAMBDA.get(r.sport, 0.08)
        c = DAMP.get(r.sport, 2.0)

        exp_gd = RH[kh] - RA[ka]                      # 예측 점수차(홈 기준)
        rows.append({"date": r.date, "year": r.year, "league": r.league,
                     "sport": r.sport, "home_team": r.home_team,
                     "away_team": r.away_team, "outcome": r.outcome,
                     "pi_diff": exp_gd,
                     "pi_home": RH[kh], "pi_away": RA[ka]})

        obs_gd = float(r.home_score - r.away_score)
        err = _psi(obs_gd - exp_gd, c)

        pending.append((kh, ka, lam, err))

    apply_updates(pending)
    return pd.DataFrame(rows)


def tune(m: pd.DataFrame, sport: str) -> tuple[float, float, float]:
    """**학습 구간에서만** (lambda, damp, gamma) 격자탐색.

    파라미터를 감으로 정하면 안 된다. 실제로 처음에 감으로 정했다가
    농구에서 Brier가 Elo보다 0.026 나빠졌다. 검증 구간은 절대 보지 않는다.
    """
    global LAMBDA, DAMP, GAMMA
    best = (None, 1e9)
    sub_all = m[m["sport"] == sport]
    if len(sub_all) < 800:
        return LAMBDA.get(sport, 0.08), DAMP.get(sport, 2.0), GAMMA

    for lam in (0.01, 0.02, 0.035, 0.05, 0.08, 0.12, 0.18):
        for dmp in (0.8, 1.5, 3.0, 6.0, 12.0, 25.0):
            for gam in (0.3, 0.5, 0.7):
                LAMBDA[sport], DAMP[sport], GAMMA = lam, dmp, gam
                pi = run_pi(sub_all)
                tr = pi[(pi["year"] <= TRAIN_END) & (pi["outcome"] != 0.5)]
                if len(tr) < 400:
                    continue
                y = (tr["outcome"] == 1.0).to_numpy(float)
                X = np.column_stack([np.ones(len(tr)), tr["pi_diff"].to_numpy(float)])
                b = _fit(X, y)
                if b is None:
                    continue
                sc = _brier(X, b, y)          # 학습 구간 성능만 본다
                if sc < best[1]:
                    best = ((lam, dmp, gam), sc)
    return best[0] if best[0] else (0.08, 2.0, 0.5)


def main() -> int:
    m = load_matches()
    print(f"경기 {len(m):,}건 · 종목별 파라미터 튜닝(학습 구간 한정)...")
    global GAMMA
    tuned = {}
    for sp in ("bs", "sc", "bk", "vl"):
        lam, dmp, gam = tune(m, sp)
        tuned[sp] = (lam, dmp, gam)
        LAMBDA[sp], DAMP[sp] = lam, dmp
        print(f"  {SPORTS[sp]}: lambda={lam} damp={dmp} gamma={gam}")
    # gamma 는 전역이라 종목별 최적의 중앙값을 쓴다
    GAMMA = float(np.median([v[2] for v in tuned.values()]))
    print(f"  gamma(공통)={GAMMA}\n")
    pi = run_pi(m)

    from features import build_features
    fe = build_features(m)
    df = fe.merge(pi[["date", "league", "home_team", "away_team", "pi_diff"]],
                  on=["date", "league", "home_team", "away_team"], how="inner")
    df = df[df["outcome"] != 0.5]
    print(f"결합 {len(df):,}건\n")

    print(f"{'종목':<6}{'학습n':>8}{'검증n':>8}{'Elo Brier':>12}"
          f"{'pi Brier':>11}{'둘다':>10}{'개선(vs Elo)':>14}  판정")
    print("-" * 84)

    for sp in ("bs", "sc", "bk", "vl"):
        sub = df[df["sport"] == sp].dropna(subset=["elo_diff", "pi_diff"])
        tr, te = sub[sub["year"] <= TRAIN_END], sub[sub["year"] > TRAIN_END]
        if len(tr) < 400 or len(te) < 200:
            continue
        y_tr = (tr["outcome"] == 1.0).to_numpy(float)
        y_te = (te["outcome"] == 1.0).to_numpy(float)

        def mk(d, cols):
            return np.column_stack([np.ones(len(d))]
                                   + [d[c].to_numpy(float) for c in cols])

        out = {}
        for name, cols in (("elo", ["elo_diff"]), ("pi", ["pi_diff"]),
                           ("both", ["elo_diff", "pi_diff"])):
            b = _fit(mk(tr, cols), y_tr)
            out[name] = _brier(mk(te, cols), b, y_te) if b is not None else np.nan

        gain = out["elo"] - out["pi"]
        v = "✅ pi 우위" if gain > 0 else "❌ Elo 우위"
        if out["both"] < min(out["elo"], out["pi"]) - 1e-6:
            v += " (둘 다 넣으면 더 좋음)"
        print(f"{SPORTS[sp]:<6}{len(tr):>8,}{len(te):>8,}{out['elo']:>12.5f}"
              f"{out['pi']:>11.5f}{out['both']:>10.5f}{gain:>+14.5f}  {v}")

    df.to_csv(Path(__file__).resolve().parent.parent / "data" / "processed"
              / "features_pi.csv", index=False)
    print("\n※ pi-ratings 는 점수차를, Elo 는 승패만 먹는다. "
          "둘을 같이 넣었을 때가 최선이면 서로 다른 정보를 담고 있다는 뜻이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
