from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from devig import market_probabilities, shin  # noqa: E402
from cross_market_edge import devig as cross_market_devig  # noqa: E402
from outcome_signal_backtest import devig as outcome_signal_devig  # noqa: E402
from combo_optimizer import pick_target_legs  # noqa: E402
from recommendation_policy import (  # noqa: E402
    MAX_AUTO_RECOMMENDATION_ODDS,
    MIN_AUTO_RECOMMENDATION_ODDS,
    recommendation_priority,
    automatic_selection_exclusion_reason,
    is_recommendable_market,
    recommendation_exclusion_reason,
    qualified_underdog,
)
import today_combo  # noqa: E402
from today_combo import (  # noqa: E402
    daily_recommendation, kickoff_at, pick_legs, ticket_metrics,
)


def candidate(event: str, probability: float, odds: float = 1.6) -> dict:
    return {
        "event_key": event,
        "market_prob": probability,
        "odds": odds,
        "bin": "1.5-1.8",
        "overround": 1.12,
        "kickoff_at": "2026-08-20T10:00:00+09:00",
        "hist_roi": -0.10,
        "hist_n": 10_000,
    }


def test_site_market_probability_is_shin():
    odds = [1.29, 4.1, 7.7]
    assert market_probabilities(odds) == shin(odds)
    assert abs(sum(market_probabilities(odds)) - 1.0) < 1e-9


def test_round_one_december_kickoff_uses_previous_calendar_year():
    kickoff = kickoff_at("12.31(목) 21:30", 2026, 1)
    assert kickoff.isoformat() == "2025-12-31T21:30:00+09:00"


def test_snapshot_backtests_share_production_market_probability():
    odds = np.asarray([1.29, 4.1, 7.7], dtype=float)
    expected = np.asarray(market_probabilities(odds.tolist()), dtype=float)

    np.testing.assert_allclose(cross_market_devig(odds), expected)
    np.testing.assert_allclose(outcome_signal_devig(odds), expected)


def test_pick_prefers_higher_final_hit_probability_inside_same_bin():
    lower = candidate("early", 0.55)
    lower["kickoff_at"] = "2026-08-20T09:00:00+09:00"
    lower["hist_roi"] = -0.04
    higher = candidate("later", 0.60)
    higher["hist_roi"] = -0.20
    assert pick_legs([lower, higher], ["1.5-1.8"]) == [higher]


def test_pick_keeps_bucket_roi_diagnostic_but_ranks_by_probability():
    better_history = candidate("better-history", 0.55)
    better_history["hist_roi"] = -0.04
    higher_market = candidate("higher-market", 0.62)
    higher_market["hist_roi"] = -0.20
    assert pick_legs([higher_market, better_history], ["1.5-1.8"]) == [higher_market]
    assert ticket_metrics([better_history])["hit_est"] == 0.55


def test_ticket_metrics_use_selected_games_not_historical_bin_average():
    first = candidate("a", 0.60, 1.65)
    second = candidate("b", 0.44, 2.05)
    metrics = ticket_metrics([first, second])
    assert metrics["actual_odds"] == 3.38
    assert metrics["hit_est"] == 0.264
    assert metrics["upset_risk"] == 0.736
    assert metrics["expected_roi"] == -0.107
    assert metrics["calibrated_expected_roi"] == metrics["expected_roi"]
    assert metrics["conservative_expected_roi"] == metrics["expected_roi"]
    assert metrics["conservative_hit_est"] == metrics["calibrated_hit_est"]
    assert metrics["independent_hit_est"] == metrics["hit_est"]
    assert metrics["market_reference_roi"] == metrics["expected_roi"]
    assert metrics["independence_assumption"] is True
    assert metrics["selection_basis"] == "final_hit_probability"
    assert metrics["historical_expected_roi"] == -0.19
    assert metrics["calibration_min_n"] is None


def test_today_combo_consumes_policy_approved_refactor_decision(monkeypatch):
    row = candidate("approved", 0.55, 1.65)
    row.update({"market": "승패", "market_label": "", "sel": "홈"})
    snapshot = {
        "decision_id": "dec-approved",
        "selection_id": "sel-approved",
        "probability": {"market": 0.55, "final": 0.63,
                        "basis": "internal-context-blend-v2"},
        "model": {"status": "operational", "promotion_gate": "passed",
                  "policy_authorized": True, "validated_edge": False,
                  "operating_version": "internal-context-blend-v2"},
        "evidence": [{"id": "team_performance"}, {"id": "lineup"}],
    }
    game = {"decision_snapshot": snapshot, "options": [{
        "selection_id": "sel-approved", "market": "승패", "label": "", "선택": "홈",
        "배당": 1.65, "시장확률": 0.55,
    }]}
    monkeypatch.setattr(today_combo, "validate_decision_snapshot", lambda value: None)

    today_combo._apply_decision_pipeline(row, game)

    assert row["predicted_hit_prob"] == 0.63
    assert row["probability_source"] == "internal-context-blend-v2"
    assert row["decision_pipeline_applied"] is True
    assert row["has_validated_edge"] is True
    assert row["decision_evidence_ids"] == ["team_performance", "lineup"]


def test_today_combo_rejects_shadow_or_different_selection(monkeypatch):
    row = candidate("shadow", 0.55, 1.65)
    row.update({"market": "승패", "market_label": "", "sel": "홈",
                "predicted_hit_prob": 0.55})
    snapshot = {
        "selection_id": "sel-away",
        "probability": {"market": 0.45, "final": 0.80, "basis": "shadow"},
        "model": {"status": "shadow", "promotion_gate": "not_passed",
                  "policy_authorized": False, "validated_edge": False},
    }
    game = {"decision_snapshot": snapshot, "options": [{
        "selection_id": "sel-away", "market": "승패", "label": "", "선택": "원정",
        "배당": 1.65, "시장확률": 0.45,
    }]}
    monkeypatch.setattr(today_combo, "validate_decision_snapshot", lambda value: None)

    today_combo._apply_decision_pipeline(row, game)

    assert row["predicted_hit_prob"] == 0.55
    assert row["decision_pipeline_applied"] is False


def test_policy_approved_event_candidate_beats_historical_fallback():
    approved = candidate("same", 0.55, 1.65)
    approved.update({"decision_pipeline_applied": True, "predicted_hit_prob": 0.63,
                     "hist_roi": -0.20})
    fallback = candidate("same", 0.60, 1.70)
    fallback.update({"decision_pipeline_applied": False, "hist_roi": -0.04})

    assert today_combo.select_event_candidates([fallback, approved]) == [approved]


def test_daily_recommendation_has_buy_challenge_and_pass_tiers():
    negative = [
        {"ok": True, "target": 3, "conservative_expected_roi": -0.05, "calibrated_hit_est": 0.269},
        {"ok": True, "target": 5, "conservative_expected_roi": -0.12, "calibrated_hit_est": 0.70},
    ]
    assert daily_recommendation(negative)["action"] == "pass"
    assert daily_recommendation(negative)["recommended_target"] == 3
    challenge = [{"ok": True, "target": 3, "actual_odds": 2.89,
                  "calibrated_hit_est": 0.282,
                  "conservative_hit_est": 0.282,
                  "conservative_expected_roi": -0.185}]
    assert daily_recommendation(challenge)["action"] == "challenge"
    assert daily_recommendation(challenge)["recommended_target"] == 3
    assert daily_recommendation(challenge)["budget_ratio"] == 0.1
    too_risky = [{"ok": True, "target": 3,
                  "calibrated_hit_est": 0.30,
                  "conservative_expected_roi": -0.206}]
    assert daily_recommendation(too_risky)["action"] == "pass"
    malformed = [{"ok": True, "target": 3,
                  "calibrated_hit_est": 0.30,
                  "conservative_expected_roi": None}]
    assert daily_recommendation(malformed)["action"] == "pass"
    positive = [{"ok": True, "target": 3, "actual_odds": 3.0,
                 "conservative_hit_est": 0.35, "conservative_expected_roi": 0.05}]
    positive[0]["has_validated_edge"] = True
    assert daily_recommendation(positive)["action"] == "buy"


def test_odd_even_is_visible_but_not_eligible_for_auto_recommendation():
    assert not is_recommendable_market("홀짝")
    assert "시장 대비 우위" in recommendation_exclusion_reason("홀짝")
    assert is_recommendable_market("승패")


def test_low_odds_are_fallback_while_high_odds_and_underdogs_are_excluded():
    assert MIN_AUTO_RECOMMENDATION_ODDS == 1.5
    assert MAX_AUTO_RECOMMENDATION_ODDS == 2.2
    assert automatic_selection_exclusion_reason("승패", 1.49, 0.68, 0.68) is None
    assert recommendation_priority(1.49) == 0
    assert recommendation_priority(1.50) == 1
    assert today_combo.bin_of(1.50) == "1.5-1.8"
    assert automatic_selection_exclusion_reason("승패", 1.5, 0.60, 0.60) is None
    assert "2.20 이상" in automatic_selection_exclusion_reason("승패", 2.2, 0.48, 0.52)
    assert "역배" in automatic_selection_exclusion_reason("승패", 2.05, 0.42, 0.58)
    assert automatic_selection_exclusion_reason("승무패", 1.95, 0.45, 0.45) is None
    assert "유효한 배당" in automatic_selection_exclusion_reason("승패", None, 0.60, 0.60)
    assert "유효한 배당" in automatic_selection_exclusion_reason("승패", 1.0, 0.60, 0.60)
    assert "시장확률" in automatic_selection_exclusion_reason("승패", 1.5, None, 0.60)


def test_qualified_underdog_is_separate_and_rejects_extremes():
    assert qualified_underdog("승패", 2.05, 0.42, 0.58, 0.55)
    assert not qualified_underdog("승패", 2.05, 0.58, 0.58, 0.70)
    assert not qualified_underdog("승패", 3.0, 0.32, 0.68, 0.55)
    assert not qualified_underdog("승패", 2.05, 0.42, 0.58, 0.95)
    assert not qualified_underdog("승패", 2.05, 0.42, 0.58, 0.49)
    assert not qualified_underdog("승패", 2.90, 0.27, 0.73, 0.50)
    assert not qualified_underdog("홀짝", 2.05, 0.42, 0.58, 0.55)


def test_target_optimizer_maximizes_joint_probability_without_fixed_bins():
    choices = [
        candidate("a", 0.80, 1.20),
        candidate("b", 0.78, 1.20),
        candidate("c", 0.60, 1.60),
        candidate("d", 0.58, 1.60),
    ]
    choices[0]["bin"] = choices[1]["bin"] = "1.0-1.3"
    picked = pick_target_legs(choices, 1.4, 2, 4)
    assert [row["event_key"] for row in picked] == ["a", "b"]
    assert math.prod(row["odds"] for row in picked) == pytest.approx(1.44)


def test_today_combo_prepares_today_and_next_morning_candidates(monkeypatch, tmp_path):
    games = []
    for game_no, market in ((1, "홀짝"), (2, "승패")):
        games.append({
            "game_no": game_no,
            "date": "08.20(목) 23:00" if game_no == 1 else "08.20(목) 23:59",
            "league": "MLB",
            "home": f"홈{game_no}",
            "away": f"원정{game_no}",
            "market": market,
            "market_label": "",
            "overround": 1.12,
            "payout": 89.3,
            "selections": [
                {"name": "홈", "odds": 1.70, "prob": 0.55},
                {"name": "원정", "odds": 2.00, "prob": 0.45},
            ],
        })
    games.append({**games[1], "game_no": 3, "date": "08.21(금) 00:00",
                  "home": "홈3", "away": "원정3"})
    games.append({**games[1], "game_no": 4, "date": "08.21(금) 12:00",
                  "home": "홈4", "away": "원정4"})
    today = tmp_path / "today.json"
    today.write_text(
        json.dumps({"year": 2026, "rounds": [{"round": 99, "games": games}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(today_combo, "TODAY", today)

    candidates = today_combo.legs_today(
        datetime(2026, 8, 20, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )

    assert len(candidates) == 2
    assert {candidate["market"] for candidate in candidates} == {"승패"}
    assert all(candidate["sel"] == "홈" for candidate in candidates)
    assert all(candidate["is_market_favorite"] is True for candidate in candidates)
    assert {candidate["game_no"] for candidate in candidates} == {2, 3}


def test_today_combo_reprices_entire_market_from_live_snapshot():
    game = {
        "game_no": 7,
        "selections": [
            {"name": "홈", "odds": 1.70, "prob": 0.55},
            {"name": "원정", "odds": 2.00, "prob": 0.45},
        ],
    }
    repriced, changed = today_combo._reprice_game(game, 99, {"99": {"7": [1.55, 2.25]}})
    assert changed is True
    assert [row["odds"] for row in repriced["selections"]] == [1.55, 2.25]
    assert sum(row["prob"] for row in repriced["selections"]) == pytest.approx(1.0, abs=1e-5)
    assert repriced["overround"] == pytest.approx(1 / 1.55 + 1 / 2.25, abs=1e-6)
    assert game["selections"][0]["odds"] == 1.70
