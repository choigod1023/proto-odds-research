"""교차 마켓 후보의 정밀검정: 야구 승패+U/O+핸디캡 → 승①패.

`cross_market_edge.py` 전수 탐색 뒤 남은 후보를 한 구조로 고정한다.
목표인 승①패와 홀짝은 입력에서 제외하고, 승패·언더오버·핸디캡만으로 잠재
스코어 분포를 적합한다. 이 파일의 결과는 사후 발견 후보의 안정성 검사이며 새로운
확증 검정이 아니다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cross_market_edge as core  # noqa: E402


TARGET_FAMILY = "승①패"
SOURCE_FAMILIES = {"승패", "승무패", "언더오버", "핸디캡"}


def build_records(current: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    market_records = []
    leg_records = []
    attempted = successful = 0
    for event_id, event in current[current["sport"] == "bs"].groupby("event_id", sort=False):
        target_rows = event[event["market_family"] == TARGET_FAMILY]
        if target_rows.empty:
            continue
        source_rows = event[event["market_family"].isin(SOURCE_FAMILIES)]
        inputs = core.collapse_inputs(source_rows)
        source_families = {item.family for item in inputs}
        # 총득점과 방향을 함께 고정하기 위한 최소 구성.
        has_main = bool(source_families & {"승패", "승무패"})
        if not has_main or "언더오버" not in source_families or "핸디캡" not in source_families:
            continue
        attempted += 1
        fit = core.fit_score_distribution("bs", inputs)
        if fit is None:
            continue
        successful += 1

        # 소스 가족을 하나씩 뺀 jackknife. 두 가족 이상이 남아야 적합한다.
        fit_variants = [fit]
        for excluded in sorted(source_families):
            reduced = [item for item in inputs if item.family != excluded]
            variant = core.fit_score_distribution("bs", reduced)
            if variant is not None:
                fit_variants.append(variant)

        accuracy_targets = (
            target_rows.sort_values("payout", ascending=False)
            .drop_duplicates("market_signature", keep="first")
        )
        for target in target_rows.itertuples(index=False):
            predictions = []
            for variant in fit_variants:
                predicted = core.model_probs(
                    "bs",
                    TARGET_FAMILY,
                    int(target.n_way),
                    target.market_label,
                    variant.lam_home,
                    variant.lam_away,
                )
                if predicted is not None:
                    predictions.append(predicted)
            if not predictions:
                continue
            predicted = predictions[0]
            prediction_sd = np.std(np.stack(predictions), axis=0, ddof=0)
            sigma = np.maximum(prediction_sd, fit.rmse)
            lower_p = np.clip(predicted - 1.645 * sigma, 0.0, 1.0)
            for selection in range(int(target.n_way)):
                odds = float(target.odds_vec[selection])
                hit = int(selection == int(target.winner_idx))
                leg_records.append(
                    {
                        "event_id": event_id,
                        "market_id": target.market_id,
                        "kickoff": target.kickoff,
                        "league": target.league,
                        "selection": selection,
                        "odds": odds,
                        "p_cross": float(predicted[selection]),
                        "p_proto": float(target.market_probs[selection]),
                        "probability_sigma": float(sigma[selection]),
                        "raw_ev": float(predicted[selection] * odds - 1.0),
                        "robust_ev": float(lower_p[selection] * odds - 1.0),
                        "fit_rmse": fit.rmse,
                        "source_markets": fit.source_markets,
                        "source_families": fit.source_families,
                        "hit": hit,
                        "ret": odds - 1.0 if hit else -1.0,
                    }
                )

        for target in accuracy_targets.itertuples(index=False):
            predicted = core.model_probs(
                "bs",
                TARGET_FAMILY,
                int(target.n_way),
                target.market_label,
                fit.lam_home,
                fit.lam_away,
            )
            if predicted is None:
                continue
            outcome = np.zeros(int(target.n_way), dtype=float)
            outcome[int(target.winner_idx)] = 1.0
            market_records.append(
                {
                    "event_id": event_id,
                    "kickoff": target.kickoff,
                    "league": target.league,
                    "target_family": TARGET_FAMILY,
                    "brier_cross": float(np.mean((predicted - outcome) ** 2)),
                    "brier_proto": float(np.mean((target.market_probs - outcome) ** 2)),
                }
            )
    return pd.DataFrame(market_records), pd.DataFrame(leg_records), {
        "attempted_events": attempted,
        "successful_events": successful,
    }


def grouped_bets(bets: pd.DataFrame, column: str) -> list[dict]:
    out = []
    if bets.empty:
        return out
    for value, group in bets.groupby(column):
        out.append(core.bet_summary(group, f"{column}={value}"))
    return out


def analyze_cutoff(data: pd.DataFrame, settled: pd.DataFrame, cutoff: int) -> dict:
    current = core.prices_at_cutoff(data, settled, cutoff)
    markets, legs, meta = build_records(current)
    primary = core.select_bets(legs, "robust_ev", 0.0, 0.05)
    split, early_markets, holdout_markets = core.chronological_split(markets)
    if split is None:
        early_bets = primary
        holdout_bets = primary
    else:
        early_bets = primary[primary["kickoff"] < split]
        holdout_bets = primary[primary["kickoff"] >= split]

    return {
        "cutoff_min": cutoff,
        "meta": meta,
        "brier": core.brier_summary(markets, "전체"),
        "brier_early": core.brier_summary(early_markets, "앞 2/3"),
        "brier_holdout": core.brier_summary(holdout_markets, "뒤 1/3"),
        "split_time": split.isoformat() if split is not None else None,
        "primary": core.bet_summary(primary, "robust_ev>0, rmse<=5%"),
        "primary_early": core.bet_summary(early_bets, "앞 2/3"),
        "primary_holdout": core.bet_summary(holdout_bets, "뒤 1/3"),
        "by_league": grouped_bets(primary, "league"),
        "by_selection": grouped_bets(primary, "selection"),
    }


def main() -> int:
    data, source = core.load_snapshots()
    settled = core.settled_markets(data)
    report = {
        "status": "post-discovery stability test; not confirmatory",
        "target": "야구 승①패",
        "sources": sorted(SOURCE_FAMILIES),
        "source_meta": source,
        "cutoffs": [analyze_cutoff(data, settled, cutoff) for cutoff in (90, 30, 10)],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
