from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from score_scenarios import (  # noqa: E402
    ScoreForecastError,
    ScoreScenario,
    forecast_from_lambdas,
    forecast_from_matrix,
    forecast_scenarios,
    get_sport_contract,
    over_probability,
)


def test_contracts_make_score_units_and_draw_settlement_explicit():
    soccer = get_sport_contract("soccer")
    baseball = get_sport_contract("bs")
    volleyball = get_sport_contract("volleyball")

    assert soccer.score_unit == "goals"
    assert soccer.primary_result_market == "1x2"
    assert soccer.condition_on_non_draw is False
    assert baseball.score_unit == "runs"
    assert baseball.condition_on_non_draw is True
    assert volleyball.score_unit == "sets"
    assert volleyball.total_market_unit == "sets"
    assert volleyball.volleyball_sets_to_win == 3


def test_joint_distribution_derives_1x2_expected_scores_and_top_scorelines():
    matrix = np.zeros((3, 3))
    matrix[1, 0] = 0.50
    matrix[1, 1] = 0.30
    matrix[0, 1] = 0.20

    forecast = forecast_from_matrix("sc", matrix, top_n=3)

    assert forecast.probability_matrix.sum() == pytest.approx(1.0)
    assert forecast.outcomes_1x2.to_dict() == pytest.approx({
        "home_win": 0.50,
        "draw": 0.30,
        "away_win": 0.20,
    })
    assert forecast.win_probabilities == forecast.outcomes_1x2
    assert forecast.expected_home_score == pytest.approx(0.80)
    assert forecast.expected_away_score == pytest.approx(0.50)
    assert [
        (row.home_score, row.away_score, row.probability)
        for row in forecast.top_scorelines
    ] == [(1, 0, 0.5), (1, 1, 0.3), (0, 1, 0.2)]
    assert forecast.to_dict()["expected_scores"]["unit"] == "goals"
    with pytest.raises(ValueError):
        forecast.probability_matrix[0, 0] = 1.0


def test_baseball_two_way_win_probability_conditions_out_model_draw_mass():
    matrix = np.zeros((3, 3))
    matrix[2, 1] = 0.50
    matrix[1, 1] = 0.30
    matrix[0, 1] = 0.20

    forecast = forecast_from_matrix("bs", matrix)

    assert forecast.outcomes_1x2.draw == pytest.approx(0.30)
    assert forecast.win_probabilities.home_win == pytest.approx(5 / 7)
    assert forecast.win_probabilities.draw == 0
    assert forecast.win_probabilities.away_win == pytest.approx(2 / 7)
    assert sum(forecast.win_probabilities.to_dict().values()) == pytest.approx(1.0)

    league_with_draw_market = forecast_from_matrix(
        "bs", matrix, condition_on_non_draw=False
    )
    assert league_with_draw_market.win_probabilities == league_with_draw_market.outcomes_1x2


def test_volleyball_keeps_only_valid_best_of_five_set_scores():
    # 일부러 불가능한 0-0, 2-2, 4-1에도 질량을 준다. 운영 계층에서 제거돼야 한다.
    raw = np.ones((7, 7), dtype=float)
    forecast = forecast_from_matrix("vl", raw, top_n=10)

    nonzero = {
        (i, j)
        for i, j in np.ndindex(forecast.probability_matrix.shape)
        if forecast.probability_matrix[i, j] > 0
    }
    assert nonzero == {
        (3, 0), (3, 1), (3, 2),
        (0, 3), (1, 3), (2, 3),
    }
    assert forecast.probability_matrix.sum() == pytest.approx(1.0)
    assert forecast.outcomes_1x2.draw == 0
    assert all(row.score_unit == "sets" for row in forecast.top_scorelines)


def test_volleyball_set_distribution_cannot_price_point_total_market():
    forecast = forecast_from_lambdas("vl", 2.8, 2.5)

    assert 0 <= over_probability(forecast, 4.5, unit="sets") <= 1
    with pytest.raises(ScoreForecastError, match="총합 단위.*sets"):
        over_probability(forecast, 180.5, unit="points")


def test_scenario_mixture_normalizes_weights_and_matrices_exactly():
    home_lineup = np.zeros((3, 3))
    home_lineup[2, 0] = 4.0  # 정규화되지 않은 입력도 개별적으로 정규화한다.
    away_lineup = np.zeros((2, 2))
    away_lineup[0, 1] = 7.0

    result = forecast_scenarios(
        "sc",
        [
            ScoreScenario("주전 출전", 2, probability_matrix=home_lineup),
            ScoreScenario("주전 결장", 1, probability_matrix=away_lineup),
        ],
        credible_mass=0.90,
    )

    assert result.probability_matrix.sum() == pytest.approx(1.0)
    assert result.probability_matrix[2, 0] == pytest.approx(2 / 3)
    assert result.probability_matrix[0, 1] == pytest.approx(1 / 3)
    assert result.outcomes_1x2.home_win == pytest.approx(2 / 3)
    assert result.expected_home_score == pytest.approx(4 / 3)
    assert result.expected_away_score == pytest.approx(1 / 3)
    assert [row.weight for row in result.scenario_contributions] == pytest.approx(
        [2 / 3, 1 / 3]
    )


def test_scenario_summary_reports_weighted_median_and_credible_interval():
    home = np.zeros((3, 3))
    home[2, 0] = 1
    away = np.zeros((2, 2))
    away[0, 1] = 1

    result = forecast_scenarios(
        "sc",
        [
            {"name": "available", "weight": 0.7, "matrix": home},
            {"name": "absent", "weight": 0.3, "matrix": away},
        ],
        credible_mass=0.90,
    )

    win = result.uncertainty["home_win_probability"]
    assert win.point == pytest.approx(0.7)
    assert win.median == 1.0
    assert (win.lower, win.upper) == (0.0, 1.0)
    home_score = result.uncertainty["expected_home_score"]
    assert home_score.point == pytest.approx(1.4)
    assert home_score.median == 2.0
    assert (home_score.lower, home_score.upper) == (0.0, 2.0)


def test_scenario_forecast_is_deterministic_and_rejects_random_features():
    scenarios = [
        {"name": "confirmed", "weight": 0.8, "lam_home": 1.5, "lam_away": 1.0},
        {"name": "changed", "weight": 0.2, "lam_home": 1.1, "lam_away": 1.3},
    ]

    first = forecast_scenarios("sc", scenarios)
    second = forecast_scenarios("sc", scenarios)
    np.testing.assert_array_equal(first.probability_matrix, second.probability_matrix)
    assert first.to_dict() == second.to_dict()

    with pytest.raises(ScoreForecastError, match="난수/미래상수"):
        forecast_scenarios("sc", [{**scenarios[0], "random_seed": 7}])


@pytest.mark.parametrize(
    "scenarios, message",
    [
        ([], "하나 이상"),
        ([ScoreScenario("a", 0, lam_home=1, lam_away=1)], "합은 0보다"),
        ([ScoreScenario("a", -1, lam_home=1, lam_away=1)], "0 이상"),
        ([ScoreScenario("a", 1, lam_home=1)], "모두 필요"),
    ],
)
def test_invalid_scenarios_fail_closed(scenarios, message):
    with pytest.raises(ScoreForecastError, match=message):
        forecast_scenarios("sc", scenarios)


def test_nonfinite_or_negative_probability_matrix_is_rejected():
    with pytest.raises(ScoreForecastError, match="NaN"):
        forecast_from_matrix("sc", np.array([[np.nan]]))
    with pytest.raises(ScoreForecastError, match="음수"):
        forecast_from_matrix("sc", np.array([[1.0, -0.1], [0.0, 0.1]]))

