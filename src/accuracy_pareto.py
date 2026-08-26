"""평균 배당을 지키면서 적중률을 높이는 선택적 예측 실험.

2024는 기반 모델 선택에 이미 사용됐고, 2025에서 선택 규칙을 정한 뒤 2026을 역사
감사한다. 낮은 배당만 고르는 편법을 막기 위해 평균 배당 1.40 이상과 30% 이상
커버리지를 고정 제약으로 둔다. 2026은 반복 확인돼 깨끗한 최종 홀드아웃이 아니다.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from historical_replay import Config, replay
from detail_paths import latest_detail_path
from devig import MARKET_PROBABILITY_METHOD
from xfip_disagreement_gate import prepare, wilson
from xfip_residual_models import blend, enrich

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "accuracy_pareto.json"
RULE_OUT = ROOT / "findings" / "accuracy_rule.json"
RULE_VERSION = "kbo-selective-accuracy-v2"
STARTER_SNAPSHOTS = ROOT / "data" / "raw" / "info_watch" / "starter_announcements.csv"


def kbo_detail_path() -> Path:
    return latest_detail_path("kbo", "baseball")


# Compatibility snapshot only; the audit resolves the cache when it runs.
KBO_DETAIL = kbo_detail_path()
ODDS_FLOORS = (1.2, 1.3, 1.4, 1.5)
COVERAGES = (.3, .4, .5, .6)
MIN_AVERAGE_ODDS = 1.40
MODEL_CONFIG = Config("nonlinear", 730)
MODEL_BLEND = 0.75


def selected_arrays(frame: pd.DataFrame, probability: np.ndarray):
    home = probability >= .5
    confidence = np.maximum(probability, 1-probability)
    odds = np.where(home, frame["o_home"], frame["o_away"])
    y = frame["y"].to_numpy(float)
    hit = np.where(home, y == 1, y == 0)
    return confidence, odds, hit


def select(frame: pd.DataFrame, probability: np.ndarray, odds_floor: float,
           coverage: float) -> np.ndarray:
    """검증 구간에서 문턱을 학습하기 위한 순위 선택. 테스트/운영에는 쓰지 않는다."""
    confidence, odds, _ = selected_arrays(frame, probability)
    eligible = np.flatnonzero(odds >= odds_floor)
    n = min(len(eligible), max(1, math.ceil(len(frame)*coverage)))
    return eligible[np.argsort(-confidence[eligible])[:n]]


def select_n(frame: pd.DataFrame, probability: np.ndarray, odds_floor: float,
             n: int) -> np.ndarray:
    """사후 진단용 동일 픽 수 순위 선택. 운영 문턱 학습에는 사용하지 않는다."""
    confidence, odds, _ = selected_arrays(frame, probability)
    eligible = np.flatnonzero(odds >= odds_floor)
    return eligible[np.argsort(-confidence[eligible])[:min(int(n), len(eligible))]]


def apply_threshold(frame: pd.DataFrame, probability: np.ndarray, odds_floor: float,
                    confidence_cutoff: float) -> np.ndarray:
    """미래 후보를 보지 않고 각 경기만으로 판정하는 운영 가능 선택."""
    confidence, odds, _ = selected_arrays(frame, probability)
    return np.flatnonzero((odds >= odds_floor) & (confidence >= confidence_cutoff))


def frozen_cutoff(value: float, decimals: int = 6) -> float:
    """경계 표본이 반올림으로 탈락하지 않는 재현 가능한 절대 문턱."""
    scale = 10**decimals
    return math.floor(float(value)*scale)/scale


def evaluate(frame: pd.DataFrame, probability: np.ndarray, indices: np.ndarray) -> dict:
    if len(indices) == 0:
        return {"n": 0, "wins": 0, "coverage": 0.0, "accuracy": None,
                "accuracy_wilson95": [None, None], "average_odds": None, "roi": None}
    _, odds, hit = selected_arrays(frame, probability)
    chosen_hit, chosen_odds = hit[indices], odds[indices]
    lo, hi = wilson(int(chosen_hit.sum()), len(indices))
    return {"n": int(len(indices)), "wins": int(chosen_hit.sum()),
            "coverage": float(len(indices)/len(frame)), "accuracy": float(chosen_hit.mean()),
            "accuracy_wilson95": [lo, hi], "average_odds": float(chosen_odds.mean()),
            "roi": float(np.mean(np.where(chosen_hit, chosen_odds-1, -1)))}


def compare_same_selected_games(frame: pd.DataFrame, p_model: np.ndarray,
                                p_market: np.ndarray, indices: np.ndarray) -> dict:
    """선별 능력과 승자 방향 능력을 섞지 않도록 동일 경기에서 비교한다."""
    if len(indices) == 0:
        return {"n": 0, "direction_disagreements": 0, "model_accuracy": None,
                "market_accuracy": None, "accuracy_difference_pp": None,
                "model_brier": None, "market_brier": None, "brier_delta": None}
    y = frame["y"].to_numpy(float)[indices]
    model = np.asarray(p_model, float)[indices]
    market = np.asarray(p_market, float)[indices]
    model_home, market_home = model >= .5, market >= .5
    model_hit = np.where(model_home, y == 1, y == 0)
    market_hit = np.where(market_home, y == 1, y == 0)
    return {
        "n": int(len(indices)),
        "direction_disagreements": int(np.sum(model_home != market_home)),
        "model_accuracy": float(model_hit.mean()),
        "market_accuracy": float(market_hit.mean()),
        "accuracy_difference_pp": float((model_hit.mean()-market_hit.mean())*100),
        "model_brier": float(np.mean((model-y)**2)),
        "market_brier": float(np.mean((market-y)**2)),
        "brier_delta": float(np.mean((model-y)**2)-np.mean((market-y)**2)),
    }


def candidates(frame: pd.DataFrame, probability: np.ndarray) -> list[dict]:
    rows = []
    for floor in ODDS_FLOORS:
        for coverage in COVERAGES:
            ranked_idx = select(frame, probability, floor, coverage)
            confidence, _, _ = selected_arrays(frame, probability)
            cutoff = (frozen_cutoff(confidence[ranked_idx].min())
                      if len(ranked_idx) else None)
            # 저장된 절대 문턱이 검증 결과 자체도 정확히 재현해야 한다. 경계 동률은
            # 운영 때와 마찬가지로 전부 포함하므로 목표 커버리지를 조금 넘을 수 있다.
            idx = (apply_threshold(frame, probability, floor, cutoff)
                   if cutoff is not None else ranked_idx)
            result = evaluate(frame, probability, idx)
            rows.append({"odds_floor": floor, "target_coverage": coverage,
                         "confidence_cutoff": cutoff, **result})
    return rows


def paired_selector_bootstrap(frame: pd.DataFrame, p_model: np.ndarray, model_idx: np.ndarray,
                              p_market: np.ndarray, market_idx: np.ndarray,
                              samples: int = 10000, seed: int = 20260826) -> dict:
    """같은 경기 기회를 날짜 블록으로 다시 뽑아 두 선택기의 적중률 차이를 비교한다."""
    if len(model_idx) == 0 or len(market_idx) == 0:
        return {"accuracy_difference_pp": None, "ci95_pp": [None, None],
                "probability_model_selector_better": None,
                "bootstrap_sampling_unit": "date" if "date" in frame else "game"}
    _, _, model_hit = selected_arrays(frame, p_model)
    _, _, market_hit = selected_arrays(frame, p_market)
    model_selected = np.zeros(len(frame), dtype=bool)
    market_selected = np.zeros(len(frame), dtype=bool)
    model_selected[model_idx] = True
    market_selected[market_idx] = True
    if "date" in frame:
        groups = pd.to_datetime(frame["date"]).dt.date.to_numpy()
        unique_groups = pd.unique(groups)
        sampling_unit = "date"
    else:
        groups = np.arange(len(frame))
        unique_groups = groups
        sampling_unit = "game"
    summaries = []
    for group in unique_groups:
        rows = groups == group
        summaries.append((int(np.sum(model_hit[rows] & model_selected[rows])),
                          int(np.sum(model_selected[rows])),
                          int(np.sum(market_hit[rows] & market_selected[rows])),
                          int(np.sum(market_selected[rows]))))
    summaries = np.asarray(summaries, dtype=float)
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(samples):
        draw = summaries[rng.integers(0, len(summaries), len(summaries))].sum(axis=0)
        if draw[1] and draw[3]:
            diffs.append(draw[0]/draw[1]-draw[2]/draw[3])
    diffs = np.asarray(diffs)
    return {"accuracy_difference_pp": float((model_hit[model_idx].mean()-market_hit[market_idx].mean())*100),
            "ci95_pp": [float(v*100) for v in np.quantile(diffs, [.025, .975])],
            "probability_model_selector_better": float(np.mean(diffs > 0)),
            "bootstrap_sampling_unit": sampling_unit}


def build_rule_artifact(validation: pd.DataFrame, chosen: dict,
                        market_cutoff: float) -> dict:
    columns = [c for c in ("date", "home_team", "away_team", "y", "p_market",
                           "p_model", "o_home", "o_away") if c in validation]
    canonical_frame = validation[columns].copy()
    numeric = canonical_frame.select_dtypes(include=[np.number]).columns
    canonical_frame[numeric] = canonical_frame[numeric].round(6)
    canonical_data = canonical_frame.to_csv(index=False, lineterminator="\n")
    payload = {
        "version": RULE_VERSION,
        "learned_on": 2025,
        "validation_data_cutoff": pd.to_datetime(validation["date"]).max().date().isoformat(),
        "validation_data_sha256": hashlib.sha256(canonical_data.encode()).hexdigest(),
        "model_config": MODEL_CONFIG.name,
        "blend_weight": MODEL_BLEND,
        "market_devig": MARKET_PROBABILITY_METHOD,
        "odds_source": "historical_settlement_archive",
        "odds_timing_status": "unknown",
        "decision_cutoff_minutes": None,
        "max_staleness_minutes": None,
        "sale_status_policy": "not_available_in_archive",
        "timezone": "Asia/Seoul",
        "operationally_valid": False,
        "odds_floor": chosen["odds_floor"],
        "target_coverage": chosen["target_coverage"],
        "model_confidence_cutoff": chosen["confidence_cutoff"],
        "market_confidence_cutoff": market_cutoff,
    }
    canonical_rule = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"))
    return {**payload, "artifact_sha256": hashlib.sha256(canonical_rule.encode()).hexdigest()}


def save_frozen_rule(rule: dict, path: Path = RULE_OUT, *, replace: bool = False) -> bool:
    """고정 규칙을 최초 저장하되 기존의 다른 규칙은 조용히 덮어쓰지 않는다."""
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != rule and not replace:
            raise RuntimeError(
                f"frozen accuracy rule differs from recomputed rule: {path}; "
                "review it and rerun with --refresh-rule to replace explicitly"
            )
        if current == rule:
            return False
    path.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def audit_starter_proxy(snapshot_path: Path = STARTER_SNAPSHOTS,
                        detail_path: Path | None = None) -> dict:
    """실제 선발을 예고 선발 대용으로 쓴 오차를 스냅샷이 있는 구간에서 잰다."""
    detail_path = kbo_detail_path() if detail_path is None else detail_path
    if not snapshot_path.exists() or not detail_path.exists():
        return {"status": "unavailable"}
    try:
        rows = pd.read_csv(snapshot_path)
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "unavailable"}
    required = {"observed_at", "gameId", "game_datetime", "league", "field",
                "value", "hours_before_game"}
    if not required.issubset(rows.columns) or not isinstance(detail, dict):
        return {"status": "unavailable"}
    rows["hours_before_game"] = pd.to_numeric(rows["hours_before_game"], errors="coerce")
    rows["observed_at"] = pd.to_datetime(rows["observed_at"], errors="coerce", utc=True)
    rows["game_datetime"] = pd.to_datetime(rows["game_datetime"], errors="coerce")
    rows = rows[(rows["league"] == "KBO") & (rows["hours_before_game"] >= 0)
                & rows["field"].isin(["homeStarterName", "awayStarterName"])]
    rows = rows.dropna(subset=["observed_at", "game_datetime"]).sort_values("observed_at")

    def compare(latest: pd.DataFrame) -> list[dict]:
        checks = []
        for row in latest.itertuples():
            game = detail.get(str(row.gameId))
            if not isinstance(game, dict):
                continue
            side = "home" if row.field == "homeStarterName" else "away"
            pitchers = (game.get("data") or {}).get(side) or []
            if not pitchers:
                continue
            announced = str(row.value).strip()
            actual = str(pitchers[0].get("name") or "").strip()
            if not announced or not actual:
                continue
            checks.append({"game_id": str(row.gameId), "side": side,
                           "announced": announced, "actual": actual,
                           "matches": announced == actual,
                           "game_date": row.game_datetime.date().isoformat()})
        return checks

    latest = rows.drop_duplicates(["gameId", "field"], keep="last")
    checks = compare(latest)
    if not checks:
        return {"status": "unavailable"}
    matches = sum(check["matches"] for check in checks)
    cutoff_match_rates = []
    for hours in (24, 6, 1):
        available = rows[rows["hours_before_game"] >= hours]
        cutoff_checks = compare(available.drop_duplicates(["gameId", "field"], keep="last"))
        cutoff_match_rates.append({
            "hours_before_game": hours,
            "announced_starter_sides": len(cutoff_checks),
            "matches": sum(check["matches"] for check in cutoff_checks),
            "match_rate": (sum(check["matches"] for check in cutoff_checks) / len(cutoff_checks)
                           if cutoff_checks else None),
        })
    return {"status": "partial_period_proxy_check",
            "period": [min(check["game_date"] for check in checks),
                       max(check["game_date"] for check in checks)],
            "games": len({check["game_id"] for check in checks}),
            "announced_starter_sides": len(checks), "matches": matches,
            "match_rate": matches/len(checks),
            "cutoff_match_rates": cutoff_match_rates,
            "mismatches": [{key: value for key, value in check.items() if key != "matches"}
                           for check in checks if not check["matches"]],
            "limitation": ("no pregame announcement snapshots before this period; legacy watcher "
                           "missed changes until this revision")}


def main(*, refresh_rule: bool = False) -> int:
    joined = prepare().sort_values("date").reset_index(drop=True)
    frame = enrich(joined.dropna(subset=["xfip_diff"])).sort_values("date").reset_index(drop=True)
    run = replay(frame, MODEL_CONFIG)
    validation = run[run["year"] == 2025].reset_index(drop=True)
    test = run[run["year"] == 2026].reset_index(drop=True)
    pv = blend(validation["p_market"].to_numpy(float), validation["p_model"].to_numpy(float), MODEL_BLEND)
    pt = blend(test["p_market"].to_numpy(float), test["p_model"].to_numpy(float), MODEL_BLEND)
    eligible = [row for row in candidates(validation, pv)
                if row["average_odds"] is not None
                and row["average_odds"] >= MIN_AVERAGE_ODDS
                and row["coverage"] >= min(COVERAGES)]
    # 작은 표본 고적중을 피하려고 적중률 자체가 아니라 Wilson 하한을 최대화한다.
    chosen = max(eligible, key=lambda row: (row["accuracy_wilson95"][0], row["n"]))
    model_idx = apply_threshold(test, pt, chosen["odds_floor"], chosen["confidence_cutoff"])
    model_result = evaluate(test, pt, model_idx)
    market_p = test["p_market"].to_numpy(float)
    market_validation = validation["p_market"].to_numpy(float)
    market_ranked = select(validation, market_validation, chosen["odds_floor"],
                           chosen["target_coverage"])
    market_confidence, _, _ = selected_arrays(validation, market_validation)
    market_cutoff = frozen_cutoff(market_confidence[market_ranked].min())
    rule_artifact = build_rule_artifact(validation, chosen, market_cutoff)
    save_frozen_rule(rule_artifact, replace=refresh_rule)
    market_idx = apply_threshold(test, market_p, chosen["odds_floor"], market_cutoff)
    market_result = evaluate(test, market_p, market_idx)
    market_same_n_idx = select_n(test, market_p, chosen["odds_floor"], len(model_idx))
    market_same_n_result = evaluate(test, market_p, market_same_n_idx)
    _, _, all_market_hit = selected_arrays(test, market_p)
    all_market_accuracy = float(all_market_hit.mean())
    comparison = paired_selector_bootstrap(test, pt, model_idx, market_p, market_idx)
    same_n_comparison = paired_selector_bootstrap(
        test, pt, model_idx, market_p, market_same_n_idx)
    same_selected = compare_same_selected_games(test, pt, market_p, model_idx)
    joined_2026 = joined[joined["year"] == 2026]
    eligible_2026 = frame[frame["year"] == 2026]
    report = {
        "protocol": "2025 learns absolute confidence cutoff; 2026 applies it per game without future ranks",
        "test_integrity": ("method-level frozen audit, but not a pristine project-level holdout: "
                           "2026 has been inspected by earlier project experiments"),
        "data_scope": {
            "feature_data_cutoff": pd.to_datetime(frame["date"]).max().date().isoformat(),
            "odds_timing": ("archived sales odds without collected_at; opening/closing time unknown; "
                            "conflicting repeated-sale prices excluded"),
            "universe_2026": {"joined_kbo_games": len(joined_2026),
                              "xfip_eligible_games": len(eligible_2026),
                              "selected_games": len(model_idx),
                              "coverage_of_joined_kbo": len(model_idx)/len(joined_2026),
                              "coverage_of_xfip_eligible": len(model_idx)/len(eligible_2026)},
            "doubleheaders": "ambiguous date/team duplicate keys excluded",
            "starter_feature": "actual box-score starter used as a historical proxy",
            "starter_proxy_audit": audit_starter_proxy(),
        },
        "constraints": {"minimum_average_odds_validation": MIN_AVERAGE_ODDS,
                        "minimum_target_coverage": min(COVERAGES),
                        "selection_metric": "Wilson 95% accuracy lower bound",
                        "accuracy_interval_assumption":
                            "binomial approximation; not adjusted for same-date dependence",
                        "comparisons": "date-block bootstrap"},
        "base_model": {"config": MODEL_CONFIG.name, "blend": MODEL_BLEND,
                       "market_devig": MARKET_PROBABILITY_METHOD},
        "frozen_rule_artifact": {"path": RULE_OUT.relative_to(ROOT).as_posix(),
                                 "sha256": rule_artifact["artifact_sha256"]},
        "validation_candidates": candidates(validation, pv), "selected_rule": chosen,
        "test_2026": {"model_selector": model_result,
                      "market_selector": {"confidence_cutoff": market_cutoff, **market_result},
                      "coverage_matched_market_diagnostic": {
                          "method": "retrospective top market confidence with same pick count",
                          "operationally_valid": False,
                          **market_same_n_result,
                          "comparison": same_n_comparison,
                      },
                      "all_market_accuracy": all_market_accuracy,
                      "selection_accuracy_vs_all_games_market_pp":
                          (model_result["accuracy"]-all_market_accuracy)*100,
                      "selection_accuracy_warning":
                          "different difficulty/coverage; not a market direction edge",
                      "same_selected_games": same_selected,
                      "different_selection_sets": comparison},
        "promotion": {
            "selective_accuracy_vs_unfiltered_market": "descriptive_only",
            "selection_edge_vs_coverage_matched_market": "descriptive_only",
            "historical_coverage_matched_ci_excludes_zero": bool(
                same_n_comparison["ci95_pp"][0] > 0),
            "market_direction_edge": ("unproven" if
                same_selected["accuracy_difference_pp"] > 0 else "reject"),
            "accuracy_profile": "research_only",
            "profit_profile": "candidate" if model_result["roi"] > 0 else "reject",
            "automatic_deployment": "reject",
            "reason": ("2026 is not a pristine project-level holdout and historical odds timing is "
                       "unknown; forward promotion requires preregistered T-30 prices, a positive "
                       "date-block confidence bound versus the frozen comparator, and positive ROI"),
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-rule", action="store_true",
                        help="reviewed methodology/data change: explicitly replace frozen rule")
    raise SystemExit(main(refresh_rule=parser.parse_args().refresh_rule))
