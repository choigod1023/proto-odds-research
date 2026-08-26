import numpy as np
import pandas as pd

from xfip_disagreement_gate import evaluate, select, wilson


def test_rule_requires_market_disagreement_and_xfip_agreement():
    frame = pd.DataFrame({
        "p_market": [.45, .45, .45], "o_home": [2.3, 2.3, 2.3],
        "o_away": [1.6, 1.6, 1.6], "xfip_diff": [.4, -.4, .4], "y": [1., 0., 1.],
    })
    rule = {"market_max": .60, "edge_min": .01, "xfip_margin": .30, "ev_min": 0.}
    mask = select(frame, np.array([.55, .55, .40]), rule)
    assert mask.tolist() == [True, False, False]


def test_evaluation_uses_selected_side_odds_and_wilson_interval():
    frame = pd.DataFrame({"y": [1., 0.], "o_home": [2., 3.], "o_away": [2., 1.5]})
    result = evaluate(frame, np.array([.6, .6]), np.array([True, True]))
    assert result["accuracy"] == .5
    assert result["roi"] == 0.0
    lo, hi = wilson(1, 2)
    assert result["accuracy_wilson95"] == [lo, hi]
