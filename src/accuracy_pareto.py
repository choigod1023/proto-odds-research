"""평균 배당을 지키면서 적중률을 높이는 선택적 예측 실험.

2024는 기반 모델 선택에 이미 사용됐고, 2025에서 선택 규칙을 정한 뒤 2026에서 한 번
검증한다. 낮은 배당만 고르는 편법을 막기 위해 평균 배당 1.40 이상과 30% 이상 커버리지를
고정 제약으로 둔다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from historical_replay import Config, replay
from xfip_disagreement_gate import prepare, wilson
from xfip_residual_models import blend, enrich

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "accuracy_pareto.json"
ODDS_FLOORS = (1.2, 1.3, 1.4, 1.5)
COVERAGES = (.3, .4, .5, .6)
MIN_AVERAGE_ODDS = 1.40
MODEL_CONFIG = Config("nonlinear", 730)
MODEL_BLEND = .75


def selected_arrays(frame: pd.DataFrame, probability: np.ndarray):
    home = probability >= .5
    confidence = np.maximum(probability, 1-probability)
    odds = np.where(home, frame["o_home"], frame["o_away"])
    y = frame["y"].to_numpy(float)
    hit = np.where(home, y == 1, y == 0)
    return confidence, odds, hit


def select(frame: pd.DataFrame, probability: np.ndarray, odds_floor: float,
           coverage: float) -> np.ndarray:
    confidence, odds, _ = selected_arrays(frame, probability)
    eligible = np.flatnonzero(odds >= odds_floor)
    n = min(len(eligible), max(1, int(len(frame)*coverage)))
    return eligible[np.argsort(-confidence[eligible])[:n]]


def evaluate(frame: pd.DataFrame, probability: np.ndarray, indices: np.ndarray) -> dict:
    _, odds, hit = selected_arrays(frame, probability)
    chosen_hit, chosen_odds = hit[indices], odds[indices]
    lo, hi = wilson(int(chosen_hit.sum()), len(indices))
    return {"n": int(len(indices)), "wins": int(chosen_hit.sum()),
            "coverage": float(len(indices)/len(frame)), "accuracy": float(chosen_hit.mean()),
            "accuracy_wilson95": [lo, hi], "average_odds": float(chosen_odds.mean()),
            "roi": float(np.mean(np.where(chosen_hit, chosen_odds-1, -1)))}


def candidates(frame: pd.DataFrame, probability: np.ndarray) -> list[dict]:
    rows = []
    for floor in ODDS_FLOORS:
        for coverage in COVERAGES:
            idx = select(frame, probability, floor, coverage)
            result = evaluate(frame, probability, idx)
            rows.append({"odds_floor": floor, "target_coverage": coverage, **result})
    return rows


def paired_selector_bootstrap(frame: pd.DataFrame, p_model: np.ndarray, model_idx: np.ndarray,
                              p_market: np.ndarray, market_idx: np.ndarray,
                              samples: int = 10000, seed: int = 20260826) -> dict:
    # 선택 집합이 달라 두 집합의 적중률 평균차를 경기 단위 비모수 부트스트랩한다.
    _, _, model_hit = selected_arrays(frame, p_model)
    _, _, market_hit = selected_arrays(frame, p_market)
    rng = np.random.default_rng(seed)
    diffs = np.empty(samples)
    for i in range(samples):
        a = rng.choice(model_idx, size=len(model_idx), replace=True)
        b = rng.choice(market_idx, size=len(market_idx), replace=True)
        diffs[i] = model_hit[a].mean()-market_hit[b].mean()
    return {"accuracy_difference_pp": float((model_hit[model_idx].mean()-market_hit[market_idx].mean())*100),
            "ci95_pp": [float(v*100) for v in np.quantile(diffs, [.025, .975])],
            "probability_model_selector_better": float(np.mean(diffs > 0))}


def main() -> int:
    frame = enrich(prepare().dropna(subset=["xfip_diff"])).sort_values("date").reset_index(drop=True)
    run = replay(frame, MODEL_CONFIG)
    validation = run[run["year"] == 2025].reset_index(drop=True)
    test = run[run["year"] == 2026].reset_index(drop=True)
    pv = blend(validation["p_market"].to_numpy(float), validation["p_model"].to_numpy(float), MODEL_BLEND)
    pt = blend(test["p_market"].to_numpy(float), test["p_model"].to_numpy(float), MODEL_BLEND)
    eligible = [row for row in candidates(validation, pv)
                if row["average_odds"] >= MIN_AVERAGE_ODDS]
    # 작은 표본 고적중을 피하려고 적중률 자체가 아니라 Wilson 하한을 최대화한다.
    chosen = max(eligible, key=lambda row: (row["accuracy_wilson95"][0], row["n"]))
    model_idx = select(test, pt, chosen["odds_floor"], chosen["target_coverage"])
    model_result = evaluate(test, pt, model_idx)
    market_p = test["p_market"].to_numpy(float)
    market_idx = select(test, market_p, chosen["odds_floor"], chosen["target_coverage"])
    market_result = evaluate(test, market_p, market_idx)
    _, _, all_market_hit = selected_arrays(test, market_p)
    all_market_accuracy = float(all_market_hit.mean())
    comparison = paired_selector_bootstrap(test, pt, model_idx, market_p, market_idx)
    report = {
        "protocol": "2025 gate selection; 2026 untouched final test",
        "constraints": {"minimum_average_odds_validation": MIN_AVERAGE_ODDS,
                        "minimum_target_coverage": min(COVERAGES),
                        "selection_metric": "Wilson 95% accuracy lower bound"},
        "base_model": {"config": MODEL_CONFIG.name, "blend": MODEL_BLEND},
        "validation_candidates": candidates(validation, pv), "selected_rule": chosen,
        "test_2026": {"model_selector": model_result, "market_selector": market_result,
                      "all_market_accuracy": all_market_accuracy,
                      "uplift_vs_all_market_pp": (model_result["accuracy"]-all_market_accuracy)*100,
                      "selector_comparison": comparison},
        "promotion": {
            "accuracy_profile": "candidate" if model_result["accuracy"] >= all_market_accuracy+.05 else "reject",
            "profit_profile": "candidate" if model_result["roi"] > 0 else "reject",
            "automatic_deployment": "reject" if model_result["accuracy_wilson95"][0] < .60 else "candidate",
            "reason": "automatic deployment requires Wilson lower bound >=60%; profit requires ROI>0",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
