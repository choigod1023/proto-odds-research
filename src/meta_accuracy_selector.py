"""기본 예측의 '맞을 가능성'을 따로 학습하는 KBO 메타 선택기.

2024 메타모델 적합, 2025 모델·절대 문턱 결정, 점수척도를 고정해 2026 역사 감사를 한다.
기본 승리확률과 정답가능성은 다른 문제이므로 분리한다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from accuracy_pareto import (MIN_AVERAGE_ODDS, MODEL_BLEND, MODEL_CONFIG, apply_threshold,
                             evaluate, frozen_cutoff, paired_selector_bootstrap,
                             select as confidence_select, selected_arrays)
from historical_replay import replay
from xfip_disagreement_gate import prepare
from xfip_residual_models import CONTEXT, blend, enrich

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "meta_accuracy_selector.json"
RIDGES = (.1, 1.0, 10.0, 100.0)
ODDS_FLOORS = (1.2, 1.3, 1.4, 1.5)
COVERAGES = (.3, .4, .5, .6)
EPS = 1e-6

BASIC = ["base_conf", "market_conf", "confidence_delta", "selected_odds",
         "market_agree", "home_pick", "xfip_aligned", "xfip_abs",
         "month_sin", "month_cos"]
DIRECTIONAL = [f"pick_{column}" for column in CONTEXT
               if column not in ("b2b_home", "b2b_away")]
FULL = BASIC + DIRECTIONAL + ["pick_b2b", "opp_b2b"]
NONLINEAR = FULL + ["base_conf_sq", "market_conf_sq", "xfip_aligned_sq",
                    "xfip_uncertainty", "confidence_odds_interaction"]
FEATURE_SETS = {"basic": BASIC, "full": FULL, "nonlinear": NONLINEAR}


def logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1-EPS)
    return np.log(p/(1-p))


def expit(x):
    x = np.clip(np.asarray(x, float), -30, 30)
    return 1/(1+np.exp(-x))


def build_meta_frame() -> pd.DataFrame:
    full = enrich(prepare().dropna(subset=["xfip_diff"])).sort_values("date").reset_index(drop=True)
    predictions = replay(full, MODEL_CONFIG)
    extra = ["date", "home_team", "away_team"] + [c for c in CONTEXT if c in full] + [
        "xfip_signed_square", "xfip_market_interaction"]
    frame = predictions.merge(full[extra], on=["date", "home_team", "away_team"], how="left")
    p_base = blend(frame["p_market"].to_numpy(float), frame["p_model"].to_numpy(float), MODEL_BLEND)
    home = p_base >= .5
    sign = np.where(home, 1., -1.)
    p_market = frame["p_market"].to_numpy(float)
    market_home = p_market >= .5
    frame["p_base"] = p_base
    frame["base_conf"] = np.maximum(p_base, 1-p_base)
    frame["market_conf"] = np.maximum(p_market, 1-p_market)
    frame["confidence_delta"] = frame["base_conf"]-frame["market_conf"]
    frame["selected_odds"] = np.where(home, frame["o_home"], frame["o_away"])
    frame["market_agree"] = (home == market_home).astype(float)
    frame["home_pick"] = home.astype(float)
    frame["xfip_aligned"] = frame["xfip_diff"]*sign
    frame["xfip_abs"] = frame["xfip_diff"].abs()
    frame["month_sin"] = np.sin(2*np.pi*frame["date"].dt.month/12)
    frame["month_cos"] = np.cos(2*np.pi*frame["date"].dt.month/12)
    for column in CONTEXT:
        if column in ("b2b_home", "b2b_away") or column not in frame:
            continue
        frame[f"pick_{column}"] = frame[column]*sign
    frame["pick_b2b"] = np.where(home, frame.get("b2b_home", 0), frame.get("b2b_away", 0))
    frame["opp_b2b"] = np.where(home, frame.get("b2b_away", 0), frame.get("b2b_home", 0))
    frame["base_conf_sq"] = frame["base_conf"]**2
    frame["market_conf_sq"] = frame["market_conf"]**2
    frame["xfip_aligned_sq"] = frame["xfip_aligned"]*frame["xfip_aligned"].abs()
    frame["xfip_uncertainty"] = frame["xfip_aligned"]*(1-np.abs(2*p_market-1))
    frame["confidence_odds_interaction"] = frame["base_conf"]*frame["selected_odds"]
    y = frame["y"].to_numpy(float)
    frame["correct"] = np.where(home, y == 1, y == 0).astype(float)
    return frame.replace([np.inf, -np.inf], np.nan)


def design(train: pd.DataFrame, other: pd.DataFrame, columns: list[str], transform=None):
    if transform is None:
        med = train[columns].median().fillna(0.)
        raw = train[columns].fillna(med).to_numpy(float)
        mean, sd = raw.mean(axis=0), raw.std(axis=0)
        sd[sd < 1e-8] = 1.
        transform = (med.tolist(), mean.tolist(), sd.tolist())
    med, mean, sd = transform
    x = (other[columns].fillna(pd.Series(med, index=columns)).to_numpy(float)-np.asarray(mean))/np.asarray(sd)
    return np.column_stack([np.ones(len(x)), x]), transform


def fit_meta(train: pd.DataFrame, columns: list[str], ridge: float):
    x, transform = design(train, train, columns)
    y = train["correct"].to_numpy(float)
    def objective(beta):
        p = np.clip(expit(x@beta), EPS, 1-EPS)
        penalty = ridge*float(beta[1:]@beta[1:])/max(1, len(train))
        return float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))+penalty)
    result = minimize(objective, np.zeros(x.shape[1]), method="L-BFGS-B")
    return result.x, transform


def predict_meta(frame: pd.DataFrame, columns: list[str], beta, transform):
    x, _ = design(frame, frame, columns, transform)
    return expit(x@beta)


def meta_logloss(frame: pd.DataFrame, p: np.ndarray) -> float:
    y = frame["correct"].to_numpy(float)
    p = np.clip(p, EPS, 1-EPS)
    return float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p)))


def meta_select(frame: pd.DataFrame, score: np.ndarray, floor: float, coverage: float) -> np.ndarray:
    eligible = np.flatnonzero(frame["selected_odds"].to_numpy(float) >= floor)
    n = min(len(eligible), max(1, math.ceil(len(frame)*coverage)))
    return eligible[np.argsort(-score[eligible])[:n]]


def apply_meta_threshold(frame: pd.DataFrame, score: np.ndarray, floor: float,
                         score_cutoff: float) -> np.ndarray:
    return np.flatnonzero((frame["selected_odds"].to_numpy(float) >= floor)
                          & (score >= score_cutoff))


def tune_gate(frame: pd.DataFrame, meta_score: np.ndarray) -> tuple[dict, list[dict]]:
    candidates = []
    p_base = frame["p_base"].to_numpy(float)
    for floor in ODDS_FLOORS:
        for coverage in COVERAGES:
            ranked_idx = meta_select(frame, meta_score, floor, coverage)
            cutoff = (frozen_cutoff(meta_score[ranked_idx].min())
                      if len(ranked_idx) else None)
            idx = (apply_meta_threshold(frame, meta_score, floor, cutoff)
                   if cutoff is not None else ranked_idx)
            result = evaluate(frame, p_base, idx)
            candidates.append({"odds_floor": floor, "target_coverage": coverage,
                               "meta_score_cutoff": cutoff, **result})
    eligible = [row for row in candidates if row["average_odds"] is not None
                and row["average_odds"] >= MIN_AVERAGE_ODDS]
    return max(eligible, key=lambda row: (row["accuracy_wilson95"][0], row["n"])), candidates


def main() -> int:
    frame = build_meta_frame()
    train = frame[frame["year"] == 2024].reset_index(drop=True)
    validation = frame[frame["year"] == 2025].reset_index(drop=True)
    test = frame[frame["year"] == 2026].reset_index(drop=True)
    trials = []
    for name, columns in FEATURE_SETS.items():
        for ridge in RIDGES:
            beta, transform = fit_meta(train, columns, ridge)
            score_v = predict_meta(validation, columns, beta, transform)
            trials.append((meta_logloss(validation, score_v), name, ridge))
    _, name, ridge = min(trials)
    columns = FEATURE_SETS[name]
    beta, transform = fit_meta(train, columns, ridge)
    score_v = predict_meta(validation, columns, beta, transform)
    gate, gate_candidates = tune_gate(validation, score_v)
    # 절대 점수 문턱을 그대로 적용해야 하므로 2025 뒤 재적합해 점수척도를 바꾸지 않는다.
    score_t = predict_meta(test, columns, beta, transform)
    meta_idx = apply_meta_threshold(test, score_t, gate["odds_floor"],
                                    gate["meta_score_cutoff"])
    meta_result = evaluate(test, test["p_base"].to_numpy(float), meta_idx)
    confidence_validation_idx = confidence_select(
        validation, validation["p_base"].to_numpy(float), gate["odds_floor"],
        gate["target_coverage"])
    validation_confidence, _, _ = selected_arrays(validation,
                                                   validation["p_base"].to_numpy(float))
    confidence_cutoff = frozen_cutoff(validation_confidence[confidence_validation_idx].min())
    confidence_idx = apply_threshold(test, test["p_base"].to_numpy(float),
                                     gate["odds_floor"], confidence_cutoff)
    confidence_result = evaluate(test, test["p_base"].to_numpy(float), confidence_idx)
    comparison = paired_selector_bootstrap(test, test["p_base"].to_numpy(float), meta_idx,
                                           test["p_base"].to_numpy(float), confidence_idx)
    historical_point_pass = bool(meta_result["accuracy"] > confidence_result["accuracy"]
                                 and meta_result["average_odds"] >= 1.4)
    historical_automatic_pass = bool(meta_result["accuracy_wilson95"][0] >= .60
                                     and meta_result["roi"] >= 0)
    report = {
        "protocol": "2024 meta fit; 2025 model/absolute cutoff; frozen 2026 historical audit",
        "test_integrity": ("not a pristine project-level holdout: 2026 has been inspected by "
                           "earlier project experiments"),
        "odds_timing": ("archived sales odds without collected_at; opening/closing time unknown; "
                        "conflicting repeated-sale prices excluded"),
        "meta_target": "probability that the frozen base pick is correct",
        "selected_meta_model": {"features": name, "ridge": ridge, "columns": columns},
        "validation_gate": gate, "validation_gate_candidates": gate_candidates,
        "test_2026": {"meta_selector": meta_result,
                      "confidence_selector": {"confidence_cutoff": confidence_cutoff,
                                              **confidence_result},
                      "comparison": comparison},
        "promotion": {"status": "reject", "automatic": "reject",
                      "historical_point_gate_pass": historical_point_pass,
                      "historical_automatic_gate_pass": historical_automatic_pass,
                      "reason": "historical audit cannot promote; use new preregistered games"},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
