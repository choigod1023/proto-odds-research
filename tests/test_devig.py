import math

import pytest

import src.devig as devig_module
from src.devig import market_probabilities


@pytest.mark.parametrize("odds", [
    [],
    [1.8],
    [0.0, 2.0],
    [1.0, 2.0],
    [float("nan"), 2.0],
    [float("inf"), 2.0],
    [float("-inf"), 2.0],
    ["broken", 2.0],
    None,
])
def test_market_probabilities_reject_invalid_market_atomically(odds):
    with pytest.raises(ValueError, match="market requires|finite numbers|market-sized"):
        market_probabilities(odds)


def test_market_probabilities_remain_finite_and_normalized():
    probabilities = market_probabilities([1.55, 2.05])

    assert len(probabilities) == 2
    assert all(math.isfinite(value) and 0.0 < value < 1.0 for value in probabilities)
    assert sum(probabilities) == pytest.approx(1.0)


def test_solver_output_must_be_normalized(monkeypatch):
    monkeypatch.setitem(
        devig_module.METHODS,
        devig_module.MARKET_PROBABILITY_METHOD,
        lambda odds: [0.4, 0.4],
    )

    probabilities = market_probabilities([1.55, 2.05])

    assert sum(probabilities) == pytest.approx(1.0)
    assert probabilities != [0.4, 0.4]
