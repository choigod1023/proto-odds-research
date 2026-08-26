import numpy as np
import pandas as pd
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from accuracy_pareto import (MODEL_BLEND, MODEL_CONFIG, apply_threshold,
                             audit_starter_proxy, build_rule_artifact, candidates,
                             compare_same_selected_games, evaluate, frozen_cutoff,
                             paired_selector_bootstrap, save_frozen_rule, select)


def test_frozen_base_model_matches_historical_replay_selection():
    report = json.loads((Path(__file__).resolve().parents[1] / "findings"
                         / "historical_replay.json").read_text(encoding="utf-8"))
    assert MODEL_CONFIG.name == report["selected"]["config"]
    assert MODEL_BLEND == report["selected"]["blend_weight"]


def test_selector_respects_odds_floor_and_coverage():
    frame = pd.DataFrame({"y": [1., 1., 0., 0.], "o_home": [1.2, 1.4, 1.6, 1.8],
                          "o_away": [2., 2., 2., 2.]})
    p = np.array([.9, .8, .7, .6])
    idx = select(frame, p, 1.4, .5)
    assert idx.tolist() == [1, 2]


def test_evaluate_reports_realized_roi():
    frame = pd.DataFrame({"y": [1., 0.], "o_home": [1.5, 2.], "o_away": [2., 2.]})
    got = evaluate(frame, np.array([.8, .8]), np.array([0, 1]))
    assert got["accuracy"] == .5
    assert got["roi"] == -.25


def test_fixed_threshold_does_not_depend_on_future_candidates():
    base = pd.DataFrame({"y": [1., 0.], "o_home": [1.4, 1.4], "o_away": [2., 2.]})
    p = np.array([.7, .6])
    before = apply_threshold(base, p, 1.3, .65).tolist()
    extended = pd.concat([base, pd.DataFrame({"y": [1.], "o_home": [1.4], "o_away": [2.]})],
                         ignore_index=True)
    after = apply_threshold(extended, np.array([.7, .6, .99]), 1.3, .65).tolist()
    assert before == [0]
    assert after[:1] == before


def test_frozen_cutoff_never_rounds_away_boundary_game():
    value = .6168766
    assert frozen_cutoff(value) == .616876
    assert value >= frozen_cutoff(value)


def test_candidate_metrics_are_reproducible_from_saved_threshold_with_ties():
    frame = pd.DataFrame({"y": [1., 1., 0., 0.], "o_home": [1.4] * 4,
                          "o_away": [2.] * 4})
    p = np.array([.9, .8, .8, .7])
    candidate = candidates(frame, p)[0]
    reproduced = apply_threshold(frame, p, candidate["odds_floor"],
                                 candidate["confidence_cutoff"])
    assert candidate["n"] == 3
    assert len(reproduced) == candidate["n"]


def test_frozen_rule_refuses_silent_replacement(tmp_path):
    path = tmp_path / "rule.json"
    assert save_frozen_rule({"version": 1}, path)
    assert not save_frozen_rule({"version": 1}, path)
    try:
        save_frozen_rule({"version": 2}, path)
    except RuntimeError as error:
        assert "differs" in str(error)
    else:
        raise AssertionError("frozen rule was silently replaced")
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert save_frozen_rule({"version": 2}, path, replace=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 2}


def test_historical_rule_cannot_masquerade_as_timed_operational_rule():
    validation = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01"]), "home_team": ["A"],
        "away_team": ["B"], "y": [1.], "p_market": [.6], "p_model": [.7],
        "o_home": [1.5], "o_away": [2.2],
    })
    chosen = {"odds_floor": 1.3, "target_coverage": .3,
              "confidence_cutoff": .62}
    rule = build_rule_artifact(validation, chosen, market_cutoff=.60)
    assert rule["odds_source"] == "historical_settlement_archive"
    assert rule["odds_timing_status"] == "unknown"
    assert rule["decision_cutoff_minutes"] is None
    assert rule["operationally_valid"] is False


def test_selector_bootstrap_resamples_shared_date_blocks():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-01",
                                                   "2026-01-02", "2026-01-02"]),
                          "y": [1., 0., 1., 0.],
                          "o_home": [1.5] * 4, "o_away": [2.] * 4})
    result = paired_selector_bootstrap(
        frame, np.array([.8, .8, .8, .8]), np.array([0, 2]),
        np.array([.8, .8, .8, .8]), np.array([1, 3]), samples=200, seed=1)
    assert result["bootstrap_sampling_unit"] == "date"
    assert result["accuracy_difference_pp"] == 100.0
    assert result["ci95_pp"] == [100.0, 100.0]


def test_same_selected_comparison_separates_direction_from_confidence():
    frame = pd.DataFrame({"y": [1., 0.], "o_home": [1.5, 1.5], "o_away": [2., 2.]})
    result = compare_same_selected_games(
        frame, np.array([.7, .7]), np.array([.6, .6]), np.array([0, 1]))
    assert result["direction_disagreements"] == 0
    assert result["accuracy_difference_pp"] == 0
    assert result["model_brier"] != result["market_brier"]


def test_starter_proxy_audit_reports_pregame_mismatch(tmp_path):
    snapshots = tmp_path / "starters.csv"
    pd.DataFrame([{ "observed_at": "2026-08-01T00:00:00Z", "gameId": "g1",
                    "game_datetime": "2026-08-02T18:30:00", "league": "KBO",
                    "field": "homeStarterName", "value": "예고", "hours_before_game": 42 }]
                 ).to_csv(snapshots, index=False)
    detail = tmp_path / "detail.json"
    detail.write_text(json.dumps({"g1": {"data": {"home": [{"name": "실제"}]}}},
                                 ensure_ascii=False), encoding="utf-8")
    result = audit_starter_proxy(snapshots, detail)
    assert result["announced_starter_sides"] == 1
    assert result["matches"] == 0
    assert result["mismatches"][0]["announced"] == "예고"


def test_starter_proxy_uses_latest_change_and_reports_cutoffs(tmp_path):
    snapshots = tmp_path / "starters.csv"
    pd.DataFrame([
        {"observed_at": "2026-08-01T00:00:00Z", "gameId": "g1",
         "game_datetime": "2026-08-02T18:30:00", "league": "KBO",
         "field": "homeStarterName", "value": "최초", "hours_before_game": 30},
        {"observed_at": "2026-08-02T11:30:00Z", "gameId": "g1",
         "game_datetime": "2026-08-02T18:30:00", "league": "KBO",
         "field": "homeStarterName", "value": "변경", "hours_before_game": 5},
    ]).to_csv(snapshots, index=False)
    detail = tmp_path / "detail.json"
    detail.write_text(json.dumps({"g1": {"data": {"home": [{"name": "변경"}]}}},
                                 ensure_ascii=False), encoding="utf-8")

    result = audit_starter_proxy(snapshots, detail)

    assert result["match_rate"] == 1.0
    cutoffs = {row["hours_before_game"]: row for row in result["cutoff_match_rates"]}
    assert cutoffs[24]["match_rate"] == 0.0
    assert cutoffs[6]["match_rate"] == 0.0
    assert cutoffs[1]["match_rate"] == 1.0
