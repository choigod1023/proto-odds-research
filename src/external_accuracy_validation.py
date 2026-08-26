"""KBO 선택 문턱이 MLB·NPB에도 통하는지 보는 전이 가능성 감사."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pandas as pd

import context_ablation as ca
from accuracy_pareto import (apply_threshold, compare_same_selected_games, evaluate,
                             paired_selector_bootstrap, select_n)
from devig import MARKET_PROBABILITY_METHOD
from xfip_residual_models import CONTEXT, blend

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "external_accuracy_validation.json"
RULE_PATH = ROOT / "findings" / "accuracy_rule.json"
TRUSTED_RULE_SHA256 = "6b293c725a2a2eba323a41fa62afd7915bef5f0e0c29b5c4a825f89a59538fcf"
WINDOW_DAYS = 730
RIDGE = 100.0


def load_frozen_rule(path: Path = RULE_PATH,
                     trusted_sha256: str = TRUSTED_RULE_SHA256) -> dict:
    rule = json.loads(path.read_text(encoding="utf-8"))
    expected = rule.pop("artifact_sha256", None)
    canonical = json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode()).hexdigest()
    if expected != actual:
        raise ValueError("accuracy rule artifact hash mismatch")
    if actual != trusted_sha256:
        raise ValueError("accuracy rule does not match the reviewed source digest")
    if rule.get("market_devig") != MARKET_PROBABILITY_METHOD:
        raise ValueError("accuracy rule devig does not match runtime")
    if rule.get("odds_timing_status") != "unknown" or rule.get("operationally_valid") is not False:
        raise ValueError("legacy historical rule timing metadata is missing or unsafe")
    return {**rule, "artifact_sha256": expected}


FROZEN_RULE = load_frozen_rule()
BLEND_WEIGHT = float(FROZEN_RULE["blend_weight"])
ODDS_FLOOR = float(FROZEN_RULE["odds_floor"])
MODEL_CONFIDENCE_CUTOFF = float(FROZEN_RULE["model_confidence_cutoff"])
MARKET_CONFIDENCE_CUTOFF = float(FROZEN_RULE["market_confidence_cutoff"])


def replay_league(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    start = frame["date"].min().to_period("M")+1
    end = frame["date"].max().to_period("M")
    for period in pd.period_range(start, end, freq="M"):
        cutoff = period.start_time
        train = frame[(frame["date"] < cutoff)
                      & (frame["date"] >= cutoff-pd.Timedelta(days=WINDOW_DAYS))]
        test = frame[frame["date"].dt.to_period("M") == period]
        if len(train) < 300 or test.empty:
            continue
        beta, transform = ca.fit_offset(train, CONTEXT, RIDGE)
        part = test.copy()
        part["p_model"] = ca.predict(test, CONTEXT, beta, transform)
        rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def all_market_accuracy(frame: pd.DataFrame) -> float:
    home = frame["p_market"].to_numpy(float) >= .5
    y = frame["y"].to_numpy(float)
    return float(((home & (y == 1)) | (~home & (y == 0))).mean())


def evaluate_year(frame: pd.DataFrame) -> dict:
    market = frame["p_market"].to_numpy(float)
    model = blend(market, frame["p_model"].to_numpy(float), BLEND_WEIGHT)
    model_idx = apply_threshold(frame, model, ODDS_FLOOR, MODEL_CONFIDENCE_CUTOFF)
    market_idx = apply_threshold(frame, market, ODDS_FLOOR, MARKET_CONFIDENCE_CUTOFF)
    model_result = evaluate(frame, model, model_idx)
    market_result = evaluate(frame, market, market_idx)
    market_same_n_idx = select_n(frame, market, ODDS_FLOOR, len(model_idx))
    market_same_n_result = evaluate(frame, market, market_same_n_idx)
    baseline = all_market_accuracy(frame)
    return {"all_games": len(frame), "model_selector": model_result,
            "market_selector": market_result, "all_market_accuracy": baseline,
            "selection_accuracy_vs_all_games_market_pp":
                (model_result["accuracy"]-baseline)*100,
            "selection_accuracy_warning":
                "different difficulty/coverage; not a market direction edge",
            "different_selection_sets_accuracy_difference_pp":
                (model_result["accuracy"]-market_result["accuracy"])*100,
            "coverage_matched_market_diagnostic": {
                "method": "retrospective top market confidence with same pick count",
                "operationally_valid": False,
                **market_same_n_result,
                "comparison": paired_selector_bootstrap(
                    frame, model, model_idx, market, market_same_n_idx),
            },
            "same_selected_games": compare_same_selected_games(
                frame, model, market, model_idx),
            "different_selection_sets": paired_selector_bootstrap(
                frame, model, model_idx, market, market_idx)}


def main() -> int:
    frame = ca.prepare()
    frame = frame[frame["sport"] == "bs"].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    leagues = {}
    for league in ("MLB", "NPB"):
        replayed = replay_league(frame[frame["league"] == league].sort_values("date"))
        leagues[league] = {}
        for year in (2025, 2026):
            result = evaluate_year(replayed[replayed["year"] == year].reset_index(drop=True))
            result["temporal_status"] = (
                "backcast_before_kbo_rule_data_cutoff" if year == 2025 else
                "after_rule_data_cutoff_but_inspected_historical_audit")
            leagues[league][str(year)] = result
    report = {
        "protocol": "KBO-derived cutoff frozen; monthly prior-only replay in external leagues",
        "validation_type": ("cutoff transportability audit, not same-model external validation: "
                            "MLB/NPB use a context-only residual model without historical xFIP"),
        "test_integrity": ("historical audit only; archived odds timing is unknown and these years "
                           "have been inspected during project development"),
        "frozen_rule": {**FROZEN_RULE, "window_days": WINDOW_DAYS, "ridge": RIDGE},
        "limitation": ("MLB/NPB lack historical xFIP detail; context residual model only; archived "
                       "sales odds lack collected_at and conflicting repeated-sale prices are excluded"),
        "leagues": leagues,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
