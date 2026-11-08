import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("domain_shift_research", Path(__file__).with_name("run.py"))
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


def test_weight_support_and_probability_invariants():
    weights = research.normalize_weights([0, 1, 100])
    assert np.isfinite(weights).all() and (weights > 0).all()
    assert np.isclose(weights.mean(), 1)
    x = np.ones((20, 1))
    market = np.tile([.4, .3, .3], (20, 1))
    beta, _ = research.fit_offset(x, market, np.arange(20) % 3, np.ones(20))
    p = research.predict(x, market, beta)
    assert np.isfinite(p).all() and np.allclose(p.sum(axis=1), 1)


def test_decimal_settlement_and_target_identical_weights():
    assert research.settle([.7, .3], [1.72, 2.0], 0) == (0, True, "0.72")
    assert research.settle([.7, .3], [1.72, 2.0], 1) == (0, False, "-1")
    market = np.array([[.6, .4], [.4, .6], [.8, .2], [.2, .8]])
    assert np.allclose(research.margin_weights(market, np.array([True] * 4)), 1)


def test_current_outcome_is_not_a_feature():
    import copy
    rows = [{"market_p": [.4, .35, .25], "odds": [2.1, 2.5, 3.2],
             "predictions": {"outcome_form": {"p": [.3, .4, .3]}}, "y": 0}]
    altered = copy.deepcopy(rows)
    altered[0]["y"] = 2
    before = research.features(rows)
    after = research.features(altered)
    assert all(np.array_equal(a, b) for a, b in zip(before, after))
