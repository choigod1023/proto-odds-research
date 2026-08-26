import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meta_accuracy_selector import apply_meta_threshold, fit_meta, meta_select, predict_meta


def test_meta_model_learns_correctness_signal():
    frame = pd.DataFrame({"base_conf": [.51, .52, .9, .95], "correct": [0., 0., 1., 1.]})
    beta, transform = fit_meta(frame, ["base_conf"], .1)
    p = predict_meta(frame, ["base_conf"], beta, transform)
    assert p[-1] > p[0]


def test_meta_selector_respects_floor_and_coverage():
    frame = pd.DataFrame({"selected_odds": [1.2, 1.3, 1.4, 1.5]})
    idx = meta_select(frame, np.array([.99, .8, .7, .6]), 1.3, .5)
    assert idx.tolist() == [1, 2]


def test_meta_threshold_is_row_local():
    frame = pd.DataFrame({"selected_odds": [1.2, 1.4, 1.5]})
    idx = apply_meta_threshold(frame, np.array([.9, .8, .7]), 1.3, .75)
    assert idx.tolist() == [1]
