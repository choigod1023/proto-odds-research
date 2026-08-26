"""xFIP를 시장확률의 연속 잔차로 결합하는 최종 시간분리 실험.

2023 적합, 2024 ridge 선택, 2023~24 재적합, 2025 시장 혼합비 선택,
2026 최종 평가는 한 번만 수행한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import context_ablation as ca
from xfip_disagreement_gate import prepare, wilson

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "xfip_residual_models.json"
CONTEXT = ca.GROUPS["team_plus_schedule"]
MODELS = {
    "market": [],
    "xfip": ["xfip_diff"],
    "context": CONTEXT,
    "context_xfip": CONTEXT + ["xfip_diff"],
    "context_xfip_nonlinear": CONTEXT + ["xfip_diff", "xfip_signed_square",
                                         "xfip_market_interaction"],
}
BLENDS = (0.0, .25, .5, .75, 1.0)
COVERAGES = (.2, .3, .5, 1.0)


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["xfip_signed_square"] = out["xfip_diff"] * out["xfip_diff"].abs()
    uncertainty = 1 - np.abs(2 * out["p_market"] - 1)
    out["xfip_market_interaction"] = out["xfip_diff"] * uncertainty
    return out.replace([np.inf, -np.inf], np.nan)


def score(frame: pd.DataFrame, p: np.ndarray) -> dict:
    y = frame["y"].to_numpy(float)
    p = np.clip(np.asarray(p), ca.EPS, 1-ca.EPS)
    home = p >= .5
    hit = np.where(home, y == 1, y == 0)
    odds = np.where(home, frame["o_home"], frame["o_away"])
    lo, hi = wilson(int(hit.sum()), len(hit))
    return {"n": int(len(hit)), "accuracy": float(hit.mean()),
            "accuracy_wilson95": [lo, hi], "brier": float(np.mean((p-y)**2)),
            "logloss": float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),
            "roi": float(np.mean(np.where(hit, odds-1, -1))),
            "average_odds": float(np.mean(odds))}


def fit_candidate(fit, tune, train, columns):
    usable = [c for c in columns if c in train.columns]
    trials = []
    for ridge in ca.RIDGES:
        beta, transform = ca.fit_offset(fit, usable, ridge)
        trials.append((score(tune, ca.predict(tune, usable, beta, transform))["logloss"], ridge))
    ridge = min(trials)[1]
    beta, transform = ca.fit_offset(train, usable, ridge)
    return usable, ridge, beta, transform


def blend(market: np.ndarray, model: np.ndarray, weight: float) -> np.ndarray:
    # 확률 자체가 아니라 log-odds를 혼합해 0/1 경계에서 안정적으로 동작한다.
    return ca.expit((1-weight)*ca.logit(market) + weight*ca.logit(model))


def paired_brier_bootstrap(frame: pd.DataFrame, model: np.ndarray,
                           samples: int = 10000, seed: int = 20260826) -> dict:
    y = frame["y"].to_numpy(float)
    market = frame["p_market"].to_numpy(float)
    delta = (np.asarray(model)-y)**2 - (market-y)**2
    rng = np.random.default_rng(seed)
    means = np.empty(samples)
    for start in range(0, samples, 500):
        size = min(500, samples-start)
        idx = rng.integers(0, len(delta), size=(size, len(delta)))
        means[start:start+size] = delta[idx].mean(axis=1)
    return {"delta": float(delta.mean()),
            "ci95": [float(v) for v in np.quantile(means, [.025, .975])],
            "probability_model_better": float(np.mean(means < 0))}


def top_ev_gate(frame: pd.DataFrame, probability: np.ndarray, coverage: float) -> dict:
    home = probability >= .5
    selected_p = np.where(home, probability, 1-probability)
    odds = np.where(home, frame["o_home"], frame["o_away"])
    expected_value = selected_p * odds - 1
    n = max(1, int(len(frame)*coverage))
    idx = np.argsort(-expected_value)[:n]
    subset = frame.iloc[idx]
    result = score(subset, probability[idx])
    result.update({"coverage": coverage, "mean_estimated_ev": float(expected_value[idx].mean())})
    return result


def main() -> int:
    frame = enrich(prepare().dropna(subset=["xfip_diff"]))
    fit = frame[frame["year"] == 2023]
    tune = frame[frame["year"] == 2024]
    train = frame[frame["year"] <= 2024]
    calibrate = frame[frame["year"] == 2025]
    test = frame[frame["year"] == 2026]
    results = {}
    for name, columns in MODELS.items():
        if name == "market":
            results[name] = {"ridge": None, "blend_weight": 0.0,
                             "calibrate": score(calibrate, calibrate["p_market"]),
                             "test": score(test, test["p_market"])}
            continue
        usable, ridge, beta, transform = fit_candidate(fit, tune, train, columns)
        p_cal_raw = ca.predict(calibrate, usable, beta, transform)
        p_test_raw = ca.predict(test, usable, beta, transform)
        market_cal = calibrate["p_market"].to_numpy(float)
        market_test = test["p_market"].to_numpy(float)
        weight = min(BLENDS, key=lambda w: score(calibrate, blend(market_cal, p_cal_raw, w))["logloss"])
        p_final = blend(market_test, p_test_raw, weight)
        results[name] = {"columns": usable, "ridge": ridge, "blend_weight": weight,
                         "calibrate_raw": score(calibrate, p_cal_raw),
                         "calibrate": score(calibrate, blend(market_cal, p_cal_raw, weight)),
                         "test_raw": score(test, p_test_raw),
                         "test": score(test, p_final),
                         "paired_brier_bootstrap": paired_brier_bootstrap(test, p_final)}
    baseline = results["market"]["test"]
    for result in results.values():
        result["test_change_vs_market"] = {
            "accuracy_pp": (result["test"]["accuracy"]-baseline["accuracy"])*100,
            "brier": result["test"]["brier"]-baseline["brier"],
            "logloss": result["test"]["logloss"]-baseline["logloss"],
            "roi_pp": (result["test"]["roi"]-baseline["roi"])*100,
        }
    best_name = min((name for name in results if name != "market"),
                    key=lambda name: results[name]["calibrate"]["logloss"])
    best = results[best_name]
    usable = best["columns"]
    _, _, beta, transform = fit_candidate(fit, tune, train, usable)
    p_cal = ca.predict(calibrate, usable, beta, transform)
    p_test = ca.predict(test, usable, beta, transform)
    chosen_coverage = max(COVERAGES, key=lambda cov: top_ev_gate(calibrate, p_cal, cov)["roi"])
    betting_gate = {"model": best_name, "selection": "2025 maximum ROI among fixed top-EV coverages",
                    "chosen_coverage": chosen_coverage,
                    "calibrate": top_ev_gate(calibrate, p_cal, chosen_coverage),
                    "test": top_ev_gate(test, p_test, chosen_coverage)}
    probability_candidates = [(name, r) for name, r in results.items() if name != "market"
                and r["test"]["brier"] < baseline["brier"]
                and r["test"]["logloss"] < baseline["logloss"]]
    proven = [(name, r) for name, r in probability_candidates
              if r["paired_brier_bootstrap"]["ci95"][1] < 0]
    betting = [(name, r) for name, r in proven if r["test"]["roi"] > 0
               and r["test"]["accuracy"] > baseline["accuracy"]]
    report = {
        "protocol": "2023 fit; 2024 ridge; <=2024 refit; 2025 blend; 2026 final test",
        "split_n": {"fit": len(fit), "tune": len(tune), "calibrate": len(calibrate), "test": len(test)},
        "models": results,
        "betting_gate": betting_gate,
        "promotion": {
            "probability_layer": "promote" if proven else "research_only",
            "betting_recommender": "promote" if betting else "reject",
            "point_estimate_candidates": [name for name, _ in probability_candidates],
            "statistically_supported": [name for name, _ in proven],
            "betting_eligible": [name for name, _ in betting],
            "rule": "probability: Brier/logloss improve and Brier CI upper<0; betting: plus ROI>0 and accuracy>market",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
