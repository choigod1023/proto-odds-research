"""시장확률 위 팀·일정 컨텍스트의 시간분리 절제 실험.

2023은 적합, 2024는 ridge 강도 선택, 2023~24로 재적합, 2025~26은 단 한 번
홀드아웃 평가한다. 시장확률을 offset으로 고정해 공개 정보가 시장의 잔차를 실제로
설명하는지만 묻는다. 결과는 findings/context_ablation.json에 저장한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from features import build_features  # noqa: E402
from matches import load_matches  # noqa: E402
from model_v2 import PI_GAMMA, PI_PARAMS, attach_odds  # noqa: E402
import pi_ratings  # noqa: E402

EPS = 1e-6
RIDGES = (0.0, 0.1, 1.0, 10.0, 100.0)
GROUPS = {
    "market": [],
    "team_state": ["elo_diff", "pi_diff", "form_diff", "margin_diff", "trend_diff",
                   "streak_diff", "venue_diff", "h2h_diff"],
    "schedule": ["rest_diff", "b2b_home", "b2b_away"],
    "team_plus_schedule": ["elo_diff", "pi_diff", "form_diff", "margin_diff",
                           "trend_diff", "streak_diff", "venue_diff", "h2h_diff",
                           "rest_diff", "b2b_home", "b2b_away"],
}


def logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def expit(x):
    x = np.clip(np.asarray(x, float), -30, 30)
    return 1 / (1 + np.exp(-x))


def prepare() -> pd.DataFrame:
    matches = load_matches()
    pi_ratings.LAMBDA = {key: value[0] for key, value in PI_PARAMS.items()}
    pi_ratings.DAMP = {key: value[1] for key, value in PI_PARAMS.items()}
    pi_ratings.GAMMA = PI_GAMMA
    pi = pi_ratings.run_pi(matches)
    frame = build_features(matches).merge(
        pi[["date", "league", "home_team", "away_team", "pi_diff"]],
        on=["date", "league", "home_team", "away_team"], how="inner")
    frame = attach_odds(frame)
    frame = frame[frame["outcome"] != 0.5].copy()
    overround = 1 / frame["o_home"] + 1 / frame["o_away"]
    frame["p_market"] = (1 / frame["o_home"]) / overround
    frame["y"] = (frame["outcome"] == 1.0).astype(float)
    return frame.replace([np.inf, -np.inf], np.nan)


def matrices(train: pd.DataFrame, other: pd.DataFrame, columns: list[str]):
    if not columns:
        return np.empty((len(train), 0)), np.empty((len(other), 0)), [], [], []
    med = train[columns].median().fillna(0.0)
    x0 = train[columns].fillna(med).to_numpy(float)
    x1 = other[columns].fillna(med).to_numpy(float)
    mean, sd = x0.mean(axis=0), x0.std(axis=0)
    sd[sd < 1e-8] = 1.0
    return (x0 - mean) / sd, (x1 - mean) / sd, med.tolist(), mean.tolist(), sd.tolist()


def fit_offset(frame: pd.DataFrame, columns: list[str], ridge: float,
               transform: tuple | None = None):
    if not columns:
        return np.zeros(0), transform
    if transform is None:
        x, _, med, mean, sd = matrices(frame, frame, columns)
        transform = (med, mean, sd)
    else:
        med, mean, sd = transform
        x = (frame[columns].fillna(pd.Series(med, index=columns)).to_numpy(float)
             - np.asarray(mean)) / np.asarray(sd)
    y = frame["y"].to_numpy(float)
    offset = logit(frame["p_market"])

    def objective(beta):
        p = np.clip(expit(offset + x @ beta), EPS, 1 - EPS)
        loss = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        return loss + ridge * float(beta @ beta) / max(1, len(frame))

    result = minimize(objective, np.zeros(len(columns)), method="L-BFGS-B")
    return result.x, transform


def predict(frame, columns, beta, transform):
    if not columns:
        return frame["p_market"].to_numpy(float)
    med, mean, sd = transform
    x = (frame[columns].fillna(pd.Series(med, index=columns)).to_numpy(float)
         - np.asarray(mean)) / np.asarray(sd)
    return expit(logit(frame["p_market"]) + x @ beta)


def metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict:
    y = frame["y"].to_numpy(float)
    p = np.clip(probability, EPS, 1 - EPS)
    pick = p >= .5
    hit = np.where(pick, y == 1, y == 0)
    odds = np.where(pick, frame["o_home"], frame["o_away"])
    profit = np.where(hit, odds - 1, -1.0)
    return {"n": int(len(frame)), "accuracy": float(hit.mean()),
            "brier": float(np.mean((p - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "roi": float(profit.mean()), "average_odds": float(np.mean(odds))}


def gates(frame: pd.DataFrame, probability: np.ndarray) -> list[dict]:
    y = frame["y"].to_numpy(float)
    market = frame["p_market"].to_numpy(float)
    confidence = np.maximum(probability, 1 - probability)
    rows = []
    for coverage in (.2, .3, .5, 1.0):
        n = max(1, int(len(frame) * coverage))
        idx = np.argsort(-confidence)[:n]
        model_home, market_home = probability[idx] >= .5, market[idx] >= .5
        model_hit = np.where(model_home, y[idx] == 1, y[idx] == 0)
        market_hit = np.where(market_home, y[idx] == 1, y[idx] == 0)
        odds = np.where(model_home, frame.iloc[idx]["o_home"], frame.iloc[idx]["o_away"])
        profit = np.where(model_hit, odds - 1, -1.0)
        rows.append({"coverage": coverage, "n": n,
                     "model_accuracy": float(model_hit.mean()),
                     "market_accuracy_same_games": float(market_hit.mean()),
                     "accuracy_uplift_pp": float((model_hit.mean() - market_hit.mean()) * 100),
                     "choice_disagreement_rate": float(np.mean(model_home != market_home)),
                     "average_odds": float(np.mean(odds)), "roi": float(profit.mean())})
    return rows


def disagreements(frame: pd.DataFrame, probability: np.ndarray) -> dict:
    y = frame["y"].to_numpy(float)
    market = frame["p_market"].to_numpy(float)
    model_home, market_home = probability >= .5, market >= .5
    selected = model_home != market_home
    if not selected.any():
        return {"n": 0}
    model_hit = np.where(model_home[selected], y[selected] == 1, y[selected] == 0)
    market_hit = np.where(market_home[selected], y[selected] == 1, y[selected] == 0)
    odds = np.where(model_home[selected], frame.loc[selected, "o_home"],
                    frame.loc[selected, "o_away"])
    profit = np.where(model_hit, odds - 1, -1.0)
    return {"n": int(selected.sum()), "coverage": float(selected.mean()),
            "model_accuracy": float(model_hit.mean()), "market_accuracy": float(market_hit.mean()),
            "accuracy_uplift_pp": float((model_hit.mean() - market_hit.mean()) * 100),
            "average_odds": float(np.mean(odds)), "roi": float(profit.mean())}


def analyze_sport(frame: pd.DataFrame, sport: str) -> dict:
    sub = frame[frame["sport"] == sport].copy()
    fit = sub[sub["year"] == 2023]
    tune = sub[sub["year"] == 2024]
    train = sub[sub["year"] <= 2024]
    test = sub[sub["year"] >= 2025]
    if min(len(fit), len(tune), len(test)) < 200:
        return {"sport": sport, "status": "insufficient_data",
                "n": {"fit": len(fit), "tune": len(tune), "test": len(test)}}
    result = {"sport": sport, "n": {"fit": len(fit), "tune": len(tune), "test": len(test)},
              "ablations": {}}
    for name, columns in GROUPS.items():
        if not columns:
            p = test["p_market"].to_numpy(float)
            result["ablations"][name] = {"ridge": None, **metrics(test, p), "gates": gates(test, p)}
            continue
        usable = [column for column in columns if column in sub.columns]
        beta0, transform0 = fit_offset(fit, usable, 0.0)
        best = min(RIDGES, key=lambda ridge: metrics(
            tune, predict(tune, usable, fit_offset(fit, usable, ridge)[0], transform0))["logloss"])
        beta, transform = fit_offset(train, usable, best)
        p = predict(test, usable, beta, transform)
        result["ablations"][name] = {"ridge": best, "columns": usable,
            **metrics(test, p), "gates": gates(test, p),
            "market_disagreements": disagreements(test, p)}
    market = result["ablations"]["market"]
    full = result["ablations"]["team_plus_schedule"]
    result["holdout_change"] = {
        "accuracy_pp": (full["accuracy"] - market["accuracy"]) * 100,
        "brier": full["brier"] - market["brier"],
        "logloss": full["logloss"] - market["logloss"],
        "roi_pp": (full["roi"] - market["roi"]) * 100,
        "five_point_target_met": full["accuracy"] >= market["accuracy"] + .05,
    }
    return result


def main() -> int:
    frame = prepare()
    report = {
        "protocol": "2023 fit; 2024 ridge selection; <=2024 refit; >=2025 untouched holdout",
        "baseline": "same-event devig Proto 2-way probability",
        "target": "absolute accuracy +5 percentage points without lower-odds substitution",
        "rows": int(len(frame)),
        "sports": [analyze_sport(frame, sport) for sport in sorted(frame["sport"].unique())],
    }
    out = ROOT / "findings" / "context_ablation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
