from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from devig import market_probabilities, shin  # noqa: E402
from recommendation_policy import (  # noqa: E402
    MAX_AUTO_RECOMMENDATION_ODDS,
    automatic_selection_exclusion_reason,
    is_recommendable_market,
    recommendation_exclusion_reason,
)
import today_combo  # noqa: E402
from today_combo import BANNED, SAFE_TARGET_BINS, pick_legs, ticket_metrics  # noqa: E402


def candidate(event: str, probability: float, odds: float = 1.6) -> dict:
    return {
        "event_key": event,
        "market_prob": probability,
        "odds": odds,
        "bin": "1.5-1.8",
        "overround": 1.12,
        "kickoff_at": "2026-08-20T10:00:00+09:00",
    }


def test_site_market_probability_is_shin():
    odds = [1.29, 4.1, 7.7]
    assert market_probabilities(odds) == shin(odds)
    assert abs(sum(market_probabilities(odds)) - 1.0) < 1e-9


def test_pick_prefers_higher_market_probability_inside_same_bin():
    lower = candidate("early", 0.55)
    lower["kickoff_at"] = "2026-08-20T09:00:00+09:00"
    higher = candidate("later", 0.60)
    assert pick_legs([lower, higher], ["1.5-1.8"]) == [higher]


def test_ticket_metrics_use_selected_games_not_historical_bin_average():
    first = candidate("a", 0.60, 1.65)
    second = candidate("b", 0.44, 2.05)
    metrics = ticket_metrics([first, second])
    assert metrics["actual_odds"] == 3.38
    assert metrics["hit_est"] == 0.264
    assert metrics["upset_risk"] == 0.736
    assert metrics["expected_roi"] == -0.107


def test_odd_even_is_visible_but_not_eligible_for_auto_recommendation():
    assert not is_recommendable_market("홀짝")
    assert "시장 대비 우위" in recommendation_exclusion_reason("홀짝")
    assert is_recommendable_market("승패")


def test_high_odds_and_market_underdog_are_not_auto_recommendations():
    assert MAX_AUTO_RECOMMENDATION_ODDS == 2.2
    assert "2.20 이상" in automatic_selection_exclusion_reason("승패", 2.2, 0.48, 0.52)
    assert "역배" in automatic_selection_exclusion_reason("승패", 2.05, 0.42, 0.58)
    assert automatic_selection_exclusion_reason("승무패", 1.95, 0.45, 0.45) is None


def test_high_targets_use_more_safe_legs_instead_of_underdog_bins():
    assert len(SAFE_TARGET_BINS[5]) == 3
    assert len(SAFE_TARGET_BINS[8]) == 3
    assert len(SAFE_TARGET_BINS[12]) == 4
    assert all(wanted_bin not in BANNED
               for bins in SAFE_TARGET_BINS.values() for wanted_bin in bins)


def test_today_combo_filters_odd_even_candidates(monkeypatch, tmp_path):
    games = []
    for game_no, market in ((1, "홀짝"), (2, "승패")):
        games.append({
            "game_no": game_no,
            "date": "08.20(목) 10:00",
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
    today = tmp_path / "today.json"
    today.write_text(
        json.dumps({"year": 2026, "rounds": [{"round": 99, "games": games}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(today_combo, "TODAY", today)

    candidates = today_combo.legs_today(
        datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    )

    assert len(candidates) == 1
    assert {candidate["market"] for candidate in candidates} == {"승패"}
    assert candidates[0]["sel"] == "홈"
    assert candidates[0]["is_market_favorite"] is True
