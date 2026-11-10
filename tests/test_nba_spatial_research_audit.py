from copy import deepcopy
from datetime import date, timedelta
import math
import pytest
from scripts.audit_nba_spatial_research import zone, features, beta_root, sigmoid, index, metrics, score_law


def game(day, identifier):
    side = dict(attempts=[20,10,8,4,0,10,20,8,0], makes=[12,4,3,1,0,4,7,2,0],
                estimated_possessions=90.8, fta=20, ftm=15)
    return dict(game_id=identifier, day=day, teams=["BOS", "LAL"], sides=[deepcopy(side), deepcopy(side)])


def test_source_target_and_future_stats_cannot_change_past_features():
    history = [game(f"2025-01-{i:02d}", str(i)) for i in range(1, 7)]
    expected = features(history, "2025-02-01", ["BOS", "LAL"])
    assert expected["spatial"] == pytest.approx(.5)
    assert expected["nonspatial"] == pytest.approx(.5)
    assert expected["feature_delta"] == pytest.approx(0)
    future = game("2025-02-01", "target")
    future["sides"] = [dict(arbitrary_target_result=9999)]
    assert features(history+[future], "2025-02-01", ["BOS", "LAL"]) == expected


def test_calendar_bounds_are_inclusive_and_recent_three_days_are_excluded():
    source = [game("2025-01-01", "lower"), game("2025-12-28", "upper")]
    source += [game(f"2025-10-{i:02d}", "mid"+str(i)) for i in range(1, 6)]
    source += [game("2024-12-31", "old"), game("2025-12-29", "recent")]
    value = features(source, "2026-01-01", ["BOS", "LAL"])
    assert value["asof_prior_games"] == 7
    assert set(value["history_game_ids"][0]) == {"lower", "upper"} | {"mid"+str(i) for i in range(1, 6)}


def test_proxy_zone_boundaries_and_missing_values_do_not_use_outcomes():
    assert zone(2,25,3.999) == 0
    assert zone(2,25,4) == 1
    assert zone(2,25,10) == 2
    assert zone(2,25,16) == 3
    assert zone(3,25,23.999) == 5
    assert zone(3,25,24) == 6
    assert zone(3,25,27) == 7
    assert zone(3,25,float("nan")) == 8
    assert zone(2,None,None) == 4


def test_coefficient_root_satisfies_regularized_convex_gradient():
    rows = [dict(feature_delta=2., market=.5, home_win=1)]
    beta = beta_root(rows)
    assert beta > 0
    assert abs(2*(sigmoid(2*beta)-1)+10*beta) < 1e-12


def test_compound_score_law_has_correct_expectation_and_variance():
    law = score_law([1.2,.7,.5], 1.)
    mean = sum(k*p for k,p in enumerate(law))
    variance = sum((k-mean)**2*p for k,p in enumerate(law))
    assert mean == pytest.approx(2*1.2+3*.7+.5)
    assert variance == pytest.approx(4*1.2+9*.7+.5)


def test_duplicate_records_and_unavailable_history_are_rejected():
    with pytest.raises(ValueError, match="duplicate_identity"):
        index([dict(id="a"),dict(id="a")])
    with pytest.raises(ValueError, match="insufficient_history"):
        features([game("2025-01-01", "a")], "2025-02-01", ["BOS","LAL"])


def test_same_price_and_home_label_arithmetic():
    rows = [dict(day="2025-01-01", home_win=1, odds=[1.8,2.0], probabilities={"spatial":.6}),
            dict(day="2025-01-01", home_win=0, odds=[1.8,2.0], probabilities={"spatial":.6})]
    result = metrics(rows,"spatial")
    assert result["hits"] == 1
    assert result["net_units"] == "-0.2"
    assert result["roi"] == -.1
    assert result["daily_mdd_units"] == "0.2"
