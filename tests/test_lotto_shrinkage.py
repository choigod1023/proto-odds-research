import itertools
import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto_shrinkage import log_normalizer, prior_weights, scores, evaluate
from lotto645 import joint_log_probability


def test_normalization_by_exhaustive_small_space():
    w = np.array([[.2, .5, 1, 2, 3], [1, 1, 1, 1, 1]])
    expected = [sum(np.prod(row[list(c)]) for c in itertools.combinations(range(5), 3)) for row in w]
    np.testing.assert_allclose(np.exp(log_normalizer(w, 3)), expected)


def test_no_future_leakage():
    a = np.zeros((800, 45)); a[:, :6] = 1
    b = a.copy(); b[740:] = np.roll(b[740:], 10, axis=1)
    np.testing.assert_array_equal(prior_weights(a, 50, 45)[:741], prior_weights(b, 50, 45)[:741])
    np.testing.assert_array_equal(scores(a)[:, :740], scores(b)[:, :740])


def test_score_matches_independent_scalar_reference():
    rng = np.random.default_rng(44)
    a = np.zeros((25, 45))
    for row in a:
        row[rng.choice(45, 6, replace=False)] = 1
    w = prior_weights(a, 50, 45)
    actual = scores(a)[1]
    expected = [joint_log_probability(np.flatnonzero(row) + 1, np.log(weight)) + math.log(math.comb(45, 6))
                for row, weight in zip(a, w)]
    np.testing.assert_allclose(actual, expected, atol=1e-12)
    np.testing.assert_array_equal(scores(a)[0], np.zeros(25))


def test_selection_frozen_and_detects_strong_persistent_signal():
    a = np.zeros((1245, 45)); a[:, :6] = 1
    b = a.copy(); b[740:] = np.roll(b[740:], 10, axis=1)
    ea, eb = evaluate(a), evaluate(b)
    assert ea["selected"] == eb["selected"] != 0
    assert ea["training_mean_log_gain"] == eb["training_mean_log_gain"]
    assert ea["splits"]["final"]["mean_log_gain"] > 0
