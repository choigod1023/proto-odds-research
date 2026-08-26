import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from accuracy_pareto import evaluate, select


def test_selector_respects_odds_floor_and_coverage():
    frame = pd.DataFrame({"y": [1., 1., 0., 0.], "o_home": [1.2, 1.4, 1.6, 1.8],
                          "o_away": [2., 2., 2., 2.]})
    p = np.array([.9, .8, .7, .6])
    idx = select(frame, p, 1.4, .5)
    assert idx.tolist() == [1, 2]


def test_evaluate_reports_realized_roi():
    frame = pd.DataFrame({"y": [1., 0.], "o_home": [1.5, 2.], "o_away": [2., 2.]})
    got = evaluate(frame, np.array([.8, .8]), np.array([0, 1]))
    assert got["accuracy"] == .5
    assert got["roi"] == -.25
