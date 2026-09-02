from __future__ import annotations

from collections import defaultdict
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matches  # noqa: E402
from generate_v2 import (  # noqa: E402
    _fixture_lambdas,
    _lambda_fields,
    _wmean,
    lambdas_for,
    market_probs,
    team_lambdas,
)
from score_dist import p_handicap, p_margin_band, p_odd, p_over, p_win  # noqa: E402
from score_scenarios import (  # noqa: E402
    ScoreForecastError,
    forecast_from_lambdas,
    probability_matrix_from_lambdas,
)


def _state(league: str, home: str, away: str, records: list[tuple[int, float]]):
    state = {
        "gf": defaultdict(list),
        "ga": defaultdict(list),
        "gfT": defaultdict(list),
        "gaT": defaultdict(list),
    }
    for bucket in ("gf", "ga"):
        state[bucket][(league, home)] = list(records)
        state[bucket][(league, away)] = list(records)
    state["gfT"][home] = list(records)
    state["gaT"][home] = list(records)
    state["gfT"][away] = list(records)
    state["gaT"][away] = list(records)
    return state


def test_operating_volleyball_matrix_contains_only_valid_best_of_five_scores():
    matrix = probability_matrix_from_lambdas("vl", 2.8, 2.5)

    nonzero = {
        (i, j)
        for i, j in np.ndindex(matrix.shape)
        if matrix[i, j] > 0
    }
    assert nonzero == {
        (3, 0), (3, 1), (3, 2),
        (0, 3), (1, 3), (2, 3),
    }
    assert matrix.sum() == pytest.approx(1.0)

    home, _, away = p_win(matrix)
    market_home, market_away = market_probs(matrix, "vl", "승패", 2, None)
    assert market_home == pytest.approx(home / (home + away))
    assert market_away == pytest.approx(away / (home + away))


def test_volleyball_point_total_and_parity_markets_fail_closed():
    matrix = probability_matrix_from_lambdas("vl", 2.8, 2.5)

    assert market_probs(matrix, "vl", "언더오버", 2, 180.5) is None
    assert market_probs(matrix, "vl", "홀짝", 2, None) is None
    # 세트 단위인 승패·핸디캡까지 함께 막은 것은 아니다.
    assert market_probs(matrix, "vl", "승패", 2, None) is not None
    assert market_probs(matrix, "vl", "핸디캡", 2, 1.5) is not None


def test_baseball_markets_share_the_same_non_draw_result_space():
    matrix = np.zeros((4, 4), dtype=float)
    matrix[0, 0] = 0.4
    matrix[3, 0] = 0.2
    matrix[2, 1] = 0.1
    matrix[0, 3] = 0.2
    matrix[1, 2] = 0.1
    original = matrix.copy()

    conditioned = matrix.copy()
    np.fill_diagonal(conditioned, 0.0)
    conditioned /= conditioned.sum()

    assert market_probs(matrix, "bs", "승패", 2, None) == pytest.approx(
        [p_win(conditioned)[0], p_win(conditioned)[2]]
    )
    assert market_probs(matrix, "bs", "홀짝", 2, None) == pytest.approx(
        [p_odd(conditioned), 1 - p_odd(conditioned)]
    )
    assert market_probs(matrix, "bs", "승①패", 3, None) == pytest.approx(
        p_margin_band(conditioned, 1)
    )
    assert market_probs(matrix, "bs", "핸디캡", 3, 0.0) == pytest.approx(
        p_handicap(conditioned, 0.0)
    )
    over = p_over(conditioned, 2.5)
    assert market_probs(matrix, "bs", "언더오버", 2, 2.5) == pytest.approx(
        [1 - over, over]
    )
    np.testing.assert_array_equal(matrix, original)


def test_basketball_margin_band_removes_impossible_draw_mass():
    matrix = np.zeros((8, 8), dtype=float)
    matrix[4, 4] = 0.4
    matrix[7, 0] = 0.2
    matrix[2, 1] = 0.1
    matrix[0, 7] = 0.2
    matrix[1, 2] = 0.1

    conditioned = matrix.copy()
    np.fill_diagonal(conditioned, 0.0)
    conditioned /= conditioned.sum()

    assert market_probs(matrix, "bk", "승⑤패", 3, None) == pytest.approx(
        p_margin_band(conditioned, 5)
    )


def test_distribution_metadata_does_not_call_rho_zero_dixon_coles():
    baseline = forecast_from_lambdas("sc", 1.4, 1.1)
    adjusted = forecast_from_lambdas("sc", 1.4, 1.1, rho=-0.05)

    assert baseline.contract.default_distribution_family == "independent_poisson"
    assert baseline.distribution_model == {
        "family": "independent_poisson",
        "rho": 0.0,
    }
    assert baseline.to_dict()["distribution_model"] == baseline.distribution_model
    assert adjusted.distribution_model == {
        "family": "dixon_coles_adjusted_poisson",
        "rho": -0.05,
    }


@pytest.mark.parametrize("invalid", [0, 2.9, True, "3.0"])
def test_invalid_volleyball_sets_to_win_is_rejected(invalid):
    with pytest.raises(ScoreForecastError, match="1 이상의 정수"):
        probability_matrix_from_lambdas(
            "vl", 2.0, 1.8, volleyball_sets_to_win=invalid
        )


def test_season_boost_is_soccer_only_and_uses_competition_season_key():
    records = [(2025, 1.0)] * 4 + [(2026, 3.0)] * 4
    expected_soccer = (4 * 1.0 + 4 * 3.0 * 2.0) / (4 + 4 * 2.0)

    assert _wmean(records, 2026, "sc") == pytest.approx(expected_soccer)
    assert _wmean(records, 2026, "bs") == pytest.approx(2.0)
    assert _wmean(records, 2026, "bk") == pytest.approx(2.0)
    assert _wmean(records, 2026, "vl") == pytest.approx(2.0)

    # EPL 2027년 3월 경기는 달력연도 2027이 아니라 2026-27 시즌이다.
    state = _state("EPL", "홈", "원정", records)
    lam = lambdas_for(
        state,
        "EPL",
        "홈",
        "원정",
        "sc",
        game_datetime=pd.Timestamp("2027-03-01 20:00"),
    )
    assert lam is not None
    assert lam[0] == pytest.approx(expected_soccer * 1.12)
    assert lam[1] == pytest.approx(expected_soccer)


def test_team_lambda_history_stores_cross_year_competition_season(monkeypatch):
    history = pd.DataFrame([{
        "kickoff": pd.Timestamp("2027-03-01 20:00"),
        "league": "EPL",
        "home_team": "홈",
        "away_team": "원정",
        "home_score": 2,
        "away_score": 1,
    }])
    monkeypatch.setattr(matches, "load_matches", lambda: history)

    state = team_lambdas()

    assert list(state["gf"][("EPL", "홈")]) == [(2026, 2)]
    assert list(state["ga"][("EPL", "홈")]) == [(2026, 1)]


def test_fixture_lambda_path_applies_tier_once_and_preserves_full_precision():
    records = [(2026, 1.23456789)] * 8
    state = _state("한국FA컵", "FC서울", "부산아이", records)
    tiers = {(2026, "서울"): 1, (2026, "부산"): 2}

    lam = _fixture_lambdas(
        state,
        year=2026,
        round_no=90,
        date_text="08.20(목) 19:00",
        league="한국FA컵",
        home="FC서울",
        away="부산아이",
        sport="sc",
        tiers=tiers,
    )

    assert lam is not None
    assert lam[2] == "리그+등급보정"
    assert lam[0] != round(lam[0], 2)
    assert lam[1] != round(lam[1], 2)
    assert _lambda_fields(lam) == {
        "lam_home": lam[0],
        "lam_away": lam[1],
        "lam_src": lam[2],
    }

    matrix = probability_matrix_from_lambdas("sc", lam[0], lam[1])
    forecast = forecast_from_lambdas("sc", lam[0], lam[1])
    np.testing.assert_array_equal(matrix, forecast.probability_matrix)
    home, draw, away = p_win(matrix)
    assert forecast.outcomes_1x2.to_dict() == pytest.approx({
        "home_win": home,
        "draw": draw,
        "away_win": away,
    })
