import numpy as np
import pandas as pd

from historical_replay import Config, calibration, replay


def test_calibration_reports_weighted_absolute_gap():
    frame = pd.DataFrame({"y": [0., 0., 1., 1., 1.]})
    got = calibration(frame, np.array([.1, .2, .7, .8, .9]))
    assert 0 <= got["ece"] <= 1
    assert sum(row["n"] for row in got["bins"]) == 5


def test_replay_never_uses_current_month_as_training(monkeypatch):
    dates = pd.date_range("2023-01-01", periods=420, freq="D")
    frame = pd.DataFrame({"date": dates, "year": dates.year, "home_team": "h", "away_team": "a",
                          "y": np.arange(420) % 2, "p_market": .5, "o_home": 2., "o_away": 2.,
                          "xfip_diff": np.arange(420, dtype=float)})
    seen = []
    def fake_fit(train, columns, ridge):
        seen.append(train["date"].max())
        return np.array([0.]), ([0.], [0.], [1.])
    monkeypatch.setattr("historical_replay.ca.fit_offset", fake_fit)
    monkeypatch.setattr("historical_replay.ca.predict", lambda test, *args: np.full(len(test), .5))
    got = replay(frame, Config("xfip", None))
    assert not got.empty
    for maximum, period in zip(seen, sorted(got["date"].dt.to_period("M").unique())):
        assert maximum < period.start_time
