"""시장 확률을 기준점으로 둔 교차 마켓 잔차 신호의 시간순 검증.

야구 승①패의 '1점차' 확률에 대해 프로토 자체의 devig 확률을 버리지 않는다.
앞 2/3 경기에서 아래 계수 beta 하나만 적합하고, 뒤 1/3 경기에 고정 적용한다.

    logit(p_blend) = logit(p_proto) + beta * (logit(p_cross) - logit(p_proto))

beta=0은 프로토 시장을 그대로 신뢰하는 기준선이다. 부트스트랩으로 beta와 예측확률의
불확실성을 전파하고, 5% 하한 확률에서도 EV가 양수일 때만 보수 베팅으로 센다.
이 검정 역시 후보를 본 뒤 만든 사후 안정성 검사이며 확증 검정은 아니다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cross_market_edge as core  # noqa: E402
import cross_market_margin_band as candidate  # noqa: E402


EPS = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


def expit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))


def predict(frame: pd.DataFrame, beta: float) -> np.ndarray:
    proto = frame["p_proto"].to_numpy(dtype=float)
    cross = frame["p_cross"].to_numpy(dtype=float)
    delta = logit(cross) - logit(proto)
    return expit(logit(proto) + beta * delta)


def fit_beta(frame: pd.DataFrame) -> float:
    y = frame["hit"].to_numpy(dtype=float)

    def objective(beta: float) -> float:
        p = np.clip(predict(frame, beta), EPS, 1.0 - EPS)
        return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

    result = minimize_scalar(objective, bounds=(-1.5, 1.5), method="bounded")
    return float(result.x)


def bootstrap_betas(frame: pd.DataFrame, seed: int, n_boot: int = 2000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot, dtype=float)
    n = len(frame)
    for index in range(n_boot):
        sample = frame.iloc[rng.integers(0, n, size=n)]
        out[index] = fit_beta(sample)
    return out


def scoring(frame: pd.DataFrame, probability: np.ndarray, label: str) -> dict:
    if frame.empty:
        return {"label": label, "n": 0}
    y = frame["hit"].to_numpy(dtype=float)
    proto = frame["p_proto"].to_numpy(dtype=float)
    probability = np.clip(np.asarray(probability, dtype=float), EPS, 1.0 - EPS)
    proto = np.clip(proto, EPS, 1.0 - EPS)
    brier_diff = (probability - y) ** 2 - (proto - y) ** 2
    logloss = -np.mean(y * np.log(probability) + (1.0 - y) * np.log(1.0 - probability))
    proto_logloss = -np.mean(y * np.log(proto) + (1.0 - y) * np.log(1.0 - proto))
    return {
        "label": label,
        "n": int(len(frame)),
        "hit_rate": float(y.mean()),
        "brier_blend": float(np.mean((probability - y) ** 2)),
        "brier_proto": float(np.mean((proto - y) ** 2)),
        "brier_difference_blend_minus_proto": float(brier_diff.mean()),
        "brier_difference_ci95": core.bootstrap_mean_ci(brier_diff),
        "logloss_blend": float(logloss),
        "logloss_proto": float(proto_logloss),
    }


def attach_predictions(
    frame: pd.DataFrame,
    beta: float,
    beta_samples: np.ndarray,
) -> pd.DataFrame:
    out = frame.copy()
    out["p_blend"] = predict(out, beta)
    proto_logit = logit(out["p_proto"].to_numpy(dtype=float))
    delta = logit(out["p_cross"].to_numpy(dtype=float)) - proto_logit
    draws = expit(proto_logit[None, :] + beta_samples[:, None] * delta[None, :])
    out["p_lower05"] = np.quantile(draws, 0.05, axis=0)
    out["raw_ev"] = out["p_blend"] * out["odds"] - 1.0
    out["robust_ev"] = out["p_lower05"] * out["odds"] - 1.0
    return out


def choose_one_run_rows(legs: pd.DataFrame) -> pd.DataFrame:
    if legs.empty:
        return legs
    rows = legs[(legs["selection"] == 1) & (legs["fit_rmse"] <= 0.05)].copy()
    # 같은 실제 경기가 여러 회차에 있으면 구매 가능한 1점차 최고 배당 한 번만 쓴다.
    return (
        rows.sort_values(["event_id", "odds"], ascending=[True, False])
        .drop_duplicates("event_id", keep="first")
        .sort_values("kickoff")
        .reset_index(drop=True)
    )


def bet_result(frame: pd.DataFrame, score: str, label: str) -> dict:
    bets = frame[frame[score] > 0].copy()
    return core.bet_summary(bets, label)


def analyze_cutoff(data: pd.DataFrame, settled: pd.DataFrame, cutoff: int) -> dict:
    current = core.prices_at_cutoff(data, settled, cutoff)
    _, legs, meta = candidate.build_records(current)
    rows = choose_one_run_rows(legs)
    split, early, holdout = core.chronological_split(rows)
    if split is None or early.empty or holdout.empty:
        return {"cutoff_min": cutoff, "n": int(len(rows)), "error": "insufficient split"}

    beta = fit_beta(early)
    beta_samples = bootstrap_betas(early, seed=20260818 + cutoff)
    early_pred = attach_predictions(early, beta, beta_samples)
    holdout_pred = attach_predictions(holdout, beta, beta_samples)

    return {
        "cutoff_min": cutoff,
        "fit_meta": meta,
        "n_events": int(len(rows)),
        "split_time": split.isoformat(),
        "beta": beta,
        "beta_bootstrap_ci95": [
            float(np.quantile(beta_samples, 0.025)),
            float(np.quantile(beta_samples, 0.975)),
        ],
        "beta_bootstrap_p_gt_zero": float(np.mean(beta_samples > 0)),
        "early_scoring": scoring(early_pred, early_pred["p_blend"].to_numpy(), "학습 앞 2/3"),
        "holdout_scoring": scoring(
            holdout_pred, holdout_pred["p_blend"].to_numpy(), "고정 적용 뒤 1/3"
        ),
        "early_raw_ev_bets": bet_result(early_pred, "raw_ev", "학습 raw EV>0"),
        "early_robust_ev_bets": bet_result(early_pred, "robust_ev", "학습 5%하한 EV>0"),
        "holdout_raw_ev_bets": bet_result(holdout_pred, "raw_ev", "홀드아웃 raw EV>0"),
        "holdout_robust_ev_bets": bet_result(
            holdout_pred, "robust_ev", "홀드아웃 5%하한 EV>0"
        ),
    }


def main() -> int:
    data, source = core.load_snapshots()
    settled = core.settled_markets(data)
    report = {
        "status": "post-discovery chronological residual validation; not confirmatory",
        "formula": "logit(p_blend)=logit(p_proto)+beta*(logit(p_cross)-logit(p_proto))",
        "target": "야구 승①패 1점차",
        "source_meta": source,
        "cutoffs": [analyze_cutoff(data, settled, cutoff) for cutoff in (90, 30, 10)],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
