import math

import pytest

from scripts.compare_frozen_multimarket_models import choose, metrics, paired_day_interval, profit


def row(**updates):
    return {"date": "2026-05-01", "fixture_id": "one", "round": "260001", "game_no": 3,
            "market": "total", "line": 8.5, "prices": {"under": 1.73, "over": 1.77},
            "outcome": "over", **updates}


def test_exact_product_payout_and_model_choice():
    r = row()
    assert choose(r, {"home_rate": 1., "away_rate": 1.}) == "under"
    assert choose(r, {"home_rate": 9., "away_rate": 9.}) == "over"
    assert profit(r, "over") == .77
    assert profit(r, "under") == -1.
    with pytest.raises(ValueError, match="no exact-product price"):
        profit(r, "home")


def test_half_handicap_is_not_original_draw_refund():
    r = row(market="handicap", line=-1.5, prices={"home": 3., "away": 1.2}, outcome="away")
    assert choose(r, {"home_rate": 1., "away_rate": 1.}) == "away"
    assert profit(r, "away") == .2
    assert profit(r, "home") == -1.


def test_roi_and_drawdown_differ_from_accuracy():
    rows = [row(fixture_id=str(i), picks={"model": "over"},
                returns={"model": p}, outcome=o) for i, (p, o) in enumerate([(-1., "under"), (.77, "over"), (-1., "under")])]
    m = metrics(rows, "model")
    assert m["hits"] == 1 and m["hit_rate"] == 1/3
    assert math.isclose(m["roi"], -1.23/3)
    assert m["max_drawdown_units"] == 1.23


def test_bootstrap_groups_days_and_identical_models_have_zero_difference():
    rows = [row(date=d, returns={"model": v, "market": v})
            for d, v in [("2026-05-01", 1.), ("2026-05-01", -1.), ("2026-05-02", 2.)]]
    m = paired_day_interval(rows, "model", iterations=100)
    assert m["day_clusters"] == 2
    assert m["roi_difference"] == 0
    assert m["descriptive_day_cluster_bootstrap95"] == [0., 0.]
