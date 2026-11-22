from datetime import date
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def research(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "experiments/nba-kleague-workload-20261106"
    monkeypatch.syspath_prepend(str(path))
    import kleague
    import nba_audit
    return kleague, nba_audit


def test_regulation_minutes_follow_replacements_and_dismissals(research):
    workload, _ = research
    assert workload.durations(["a", "b"], [(45, "a", "c"), (60, "b", None), (75, "c", "d")]) == {
        "a": 45, "b": 60, "c": 30, "d": 15,
    }
    assert workload.durations(["a"], [(50, "b", "c")]) is None


def test_lagged_features_cannot_see_today_lineup_or_minutes(research):
    workload, _ = research
    home = [f"h{i}" for i in range(11)]
    away = [f"a{i}" for i in range(11)]
    games = [{"id": str(i), "date": date(2026, 6, day), "teams": ["H", "A"], "xi": [home, away]}
             for i, day in enumerate((1, 5, 9, 12))]
    minutes = {g["id"]: {"H": dict.fromkeys(home, 60), "A": dict.fromkeys(away, 90)} for g in games}
    before = workload.features(games, minutes, False)["2"]
    np.testing.assert_allclose(before, [-1 / 3, 0, 0])
    games[2]["xi"] = [["replacement", *home[1:]], away]
    minutes["2"] = None
    np.testing.assert_array_equal(workload.features(games, minutes, False)["2"], before)
    assert workload.features(games, minutes, True)["2"][2] == pytest.approx(1 / 11)
    assert "3" not in workload.features(games, minutes, False)


def test_unchanged_model_and_roi_use_same_quote(research):
    workload, _ = research
    base = np.array([[0.4, 0.2, 0.4]])
    np.testing.assert_allclose(workload.probabilities(base, np.ones((1, 3)), np.zeros(3)), base)
    row = {"date": "2026-09-01", "result": 2, "odds": [2.0, 3.0, 4.0], "prior": 0, "workload": 2,
           "prior_p": [0.5, 0.1, 0.4], "workload_p": [0.4, 0.1, 0.5]}
    result = workload.summarize([row])
    assert result["arms"]["prior"]["roi"] == -1
    assert result["arms"]["workload"]["roi"] == 3
    assert result["changed"] == 1


def test_official_injury_requires_actual_pregame_receipt(research):
    _, nba = research
    row = {"payload": {"information_type": "injury_report"},
           "source_url": "https://ak-static.cms.nba.com/referee/injury/example.pdf",
           "timing_basis": "actual_receipt", "timestamp_basis": "http_response_body_completed",
           "kickoff": "2026-11-05T00:00:00+00:00", "received_at": "2026-11-04T22:00:00+00:00"}
    assert nba.injury_before_tip(row)
    assert not nba.injury_before_tip({**row, "received_at": row["kickoff"]})
    assert not nba.injury_before_tip({**row, "timestamp_basis": "unknown"})
