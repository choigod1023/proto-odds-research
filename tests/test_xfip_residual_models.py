import numpy as np
import pandas as pd

from xfip_residual_models import blend, enrich, paired_brier_bootstrap, top_ev_gate


def test_enrich_preserves_xfip_direction_in_nonlinear_features():
    frame = pd.DataFrame({"xfip_diff": [-2., 3.], "p_market": [.5, .75]})
    got = enrich(frame)
    assert got["xfip_signed_square"].tolist() == [-4., 9.]
    assert got["xfip_market_interaction"].tolist() == [-2., 1.5]


def test_blend_endpoints_equal_market_and_model():
    market = np.array([.2, .8])
    model = np.array([.4, .6])
    assert np.allclose(blend(market, model, 0), market)
    assert np.allclose(blend(market, model, 1), model)


def test_paired_bootstrap_detects_perfect_model_improvement():
    frame = pd.DataFrame({"y": [0., 1.] * 20, "p_market": [.5, .5] * 20})
    model = frame["y"].to_numpy() * .8 + .1
    got = paired_brier_bootstrap(frame, model, samples=500, seed=1)
    assert got["ci95"][1] < 0
    assert got["probability_model_better"] == 1.0


def test_top_ev_gate_keeps_requested_coverage():
    frame = pd.DataFrame({"y": [1., 0., 1., 0.], "o_home": [2., 2., 2., 2.],
                          "o_away": [2., 2., 2., 2.]})
    got = top_ev_gate(frame, np.array([.9, .8, .7, .6]), .5)
    assert got["n"] == 2
    assert got["coverage"] == .5
