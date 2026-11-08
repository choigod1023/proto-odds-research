import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.special import expit

path = Path(__file__).with_name("run.py")
spec = importlib.util.spec_from_file_location("kbo_market_residual", path)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def row(date, winner, market, m0):
    return {"date": date, "winner": winner}, {}, market, m0


def test_gate_is_market_offset_and_symmetric():
    rows = [row("2024-05-01", "home", 0.5, 0.7),
            row("2024-05-01", "away", 0.5, 0.3),
            row("2024-05-01", "home", 0.75, 0.6)]
    offset, features = model.design(rows, True)
    assert np.allclose(expit(offset), [0.5, 0.5, 0.75])
    assert np.allclose(expit(offset + features @ [1, 1]), [0.7, 0.3, 0.6])
    assert np.allclose(expit(offset + features @ [1, 0]), [0.7, 0.3, 0.75])


def test_fit_learns_informative_input_without_using_evaluation_results():
    training = [row("2024-05-01", "home", 0.5, 0.75),
                row("2024-05-02", "away", 0.5, 0.25)]
    first = [row("2025-05-01", "home", 0.8, 0.3)]
    second = [row("2025-05-01", "away", 0.2, 0.7)]
    weights = model.train(training, first, True)
    assert weights[0] > 0.9
    assert weights == model.train(training, second, True)
    with pytest.raises(ValueError, match="training dates"):
        model.train(first, training, True)
