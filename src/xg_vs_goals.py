"""xG 가 득점보다 나은가 — 관문 실험.

이 프로젝트의 목표는 **시장보다 나은 확률을 잡아 적중시키는 것**이다.
그 전에 통과해야 할 관문이 있다:

    팀의 최근 **xG** 기록이 최근 **득점** 기록보다 다음 경기를 잘 맞히는가?

여기서 지면 K리그 xG 를 기다리거나(2027년) 돈을 쓸 이유가 없다.
이기면 그때 비용을 치를 근거가 생긴다.

왜 지금 답할 수 있나
--------------------
K리그 경기별 xG 이력은 세 소스 모두 robots 로 막혀 있다. 그런데 StatsBomb
오픈데이터에 **완전한 시즌 4개(1,517경기)** 가 슛 단위 xG 와 함께 공개돼 있다.
2015/16 이라 그대로 베팅에 쓸 모델은 아니지만, **원리 검증에는 충분하다.**

규율
----
· 시간 순서 엄수 — 각 경기의 피처는 그 경기 **이전** 기록만
· 학습/검증도 시간으로 분리 (시즌 전반 학습 / 후반 검증)
· 결과 지표(득점)와 과정 지표(xG)를 **같은 자리에서** 붙인다
· 점추정만 보고 판단하지 않는다 — 부트스트랩 신뢰구간까지 본다
  (박빙 대조에서 −0.00029 를 '모델 우위'로 읽을 뻔했다)
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variable_impact import _brier, _fit, _se           # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "processed" / "statsbomb_xg.csv"
WINDOW = 8            # 최근 몇 경기로 폼을 잴 것인가
MIN_GAMES = 5         # 이만큼은 쌓여야 폼으로 인정


def load() -> pd.DataFrame:
    d = pd.read_csv(DATA)
    d["date"] = pd.to_datetime(d["date"])
    d = d.drop_duplicates(subset=["match_id"]).sort_values(["date", "match_id"])
    return d.reset_index(drop=True)


def build_form(d: pd.DataFrame) -> pd.DataFrame:
    """walk-forward 폼 — 각 경기의 피처는 그 경기 이전 기록만 쓴다.

    리그별로 따로 센다. 리그마다 득점 수준이 다르므로 섞으면 없는 신호가 생긴다.
    """
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    def form(t):
        v = list(hist[t])
        if len(v) < MIN_GAMES:
            return None
        return {k: float(np.mean([x[k] for x in v]))
                for k in ("gf", "ga", "xgf", "xga", "npxgf", "npxga", "sotf")}

    for r in d.itertuples():
        h, a = form((r.league, r.home_team)), form((r.league, r.away_team))
        out = {"match_id": r.match_id, "date": r.date, "league": r.league,
               "home_team": r.home_team, "away_team": r.away_team}
        if h and a:
            # 결과 지표 — 실제 득실차
            out["goal_diff"] = (h["gf"] - h["ga"]) - (a["gf"] - a["ga"])
            # 과정 지표 — xG 득실차
            out["xg_diff"] = (h["xgf"] - h["xga"]) - (a["xgf"] - a["xga"])
            # 과정 지표(페널티 제외) — 문헌 표준
            out["npxg_diff"] = (h["npxgf"] - h["npxga"]) - (a["npxgf"] - a["npxga"])
            # 유효슈팅 — K리그에서 썼던 거친 대용품. 같이 붙여 비교한다
            out["sot_diff"] = h["sotf"] - a["sotf"]
            # 운 지표 — 득점이 xG 를 얼마나 초과했나. 높으면 되돌아갈 몫
            out["luck_diff"] = ((h["gf"] - h["xgf"]) - (a["gf"] - a["xgf"]))
        else:
            for k in ("goal_diff", "xg_diff", "npxg_diff", "sot_diff", "luck_diff"):
                out[k] = np.nan
        rows.append(out)

        for t, gf, ga, xgf, xga, nf, na, sf in (
                ((r.league, r.home_team), r.home_score, r.away_score,
                 r.h_xg, r.a_xg, r.h_npxg, r.a_npxg, r.h_sot),
                ((r.league, r.away_team), r.away_score, r.home_score,
                 r.a_xg, r.h_xg, r.a_npxg, r.h_npxg, r.a_sot)):
            hist[t].append({"gf": gf, "ga": ga, "xgf": xgf, "xga": xga,
                            "npxgf": nf, "npxga": na, "sotf": sf})
    return pd.DataFrame(rows)


FEATS = ["goal_diff", "xg_diff", "npxg_diff", "sot_diff", "luck_diff"]
LABELS = {
    "goal_diff": "득실 차 (결과 지표)",
    "xg_diff": "xG 차 ⭐ 과정 지표",
    "npxg_diff": "npxG 차 ⭐ 과정(페널티 제외)",
    "sot_diff": "유효슈팅 차 (거친 대용품)",
    "luck_diff": "득점−xG 차 (운 지표)",
}


def main() -> int:
    if not DATA.exists():
        print(f"{DATA} 없음 — 먼저 python src/statsbomb_xg.py")
        return 1
    d = load()
    print(f"StatsBomb {len(d):,}경기 ({d['date'].min().date()} ~ {d['date'].max().date()})")
    print(f"  리그: {', '.join(sorted(d['league'].unique()))}")
    print(f"  평균 xG 홈 {d['h_xg'].mean():.2f} / 원정 {d['a_xg'].mean():.2f}"
          f" · 평균 득점 홈 {d['home_score'].mean():.2f} / 원정 {d['away_score'].mean():.2f}")

    ff = build_form(d)
    ff = ff.merge(d[["match_id", "home_score", "away_score"]], on="match_id")
    # 무승부는 제외 — 2-way(홈 승/패) 로 본다. K리그 검증과 같은 처리.
    ff = ff[ff["home_score"] != ff["away_score"]].copy()
    ff["y"] = (ff["home_score"] > ff["away_score"]).astype(float)
    ff = ff.dropna(subset=FEATS)

    # 시간 분리 — 앞 60% 학습 / 뒤 40% 검증
    ff = ff.sort_values("date").reset_index(drop=True)
    cut = int(len(ff) * 0.6)
    tr, te = ff.iloc[:cut], ff.iloc[cut:]
    print(f"\n무승부 제외·폼 확보 {len(ff):,} · 학습 {len(tr):,} / 검증 {len(te):,}")
    print(f"  학습 ~{tr['date'].max().date()} / 검증 {te['date'].min().date()}~")

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr, y_te = tr["y"].to_numpy(float), te["y"].to_numpy(float)

    # 기준선 — 홈 어드밴티지만 (절편만). 여기서 얼마나 나아지는지를 본다.
    base = float(np.mean((y_tr.mean() - y_te) ** 2))
    print(f"\n기준선(홈승률 고정) 검증 Brier = {base:.5f}")
    print(f"\n{'피처':<30}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}")
    print("-" * 71)

    got, preds = {}, {}
    for f in FEATS:
        b = _fit(mk(tr, [f]), y_tr)
        if b is None:
            continue
        z = b[1] / _se(mk(tr, [f]), b)[1]
        br = _brier(mk(te, [f]), b, y_te)
        got[f] = base - br
        preds[f] = 1 / (1 + np.exp(-np.clip(mk(te, [f]) @ b, -30, 30)))
        print(f"{LABELS[f]:<30}{b[1]:>10.4f}{z:>8.2f}{br:>10.5f}{base-br:>+11.5f}")

    # ---- 정면 비교 + 부트스트랩
    if "goal_diff" in preds and "xg_diff" in preds:
        print("\n" + "=" * 71)
        print("⭐ 결과(득실차) vs 과정(xG) 정면 비교")
        for k in ("xg_diff", "npxg_diff"):
            if k not in preds:
                continue
            diff = (preds[k] - y_te) ** 2 - (preds["goal_diff"] - y_te) ** 2
            rng = np.random.default_rng(42)
            idx = rng.integers(0, len(diff), size=(10000, len(diff)))
            boot = diff[idx].mean(axis=1)
            lo, hi = np.percentile(boot, [2.5, 97.5])
            win = float((boot < 0).mean())
            verdict = ("과정 우위 유의" if hi < 0 else
                       "결과 우위 유의" if lo > 0 else "판정 불가")
            print(f"  {LABELS[k]:<28}{diff.mean():+.5f}  "
                  f"95% CI [{lo:+.5f}, {hi:+.5f}]  우위확률 {win:.1%}  → {verdict}")
        print("\n  (음수 = xG 가 득실차보다 낫다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
