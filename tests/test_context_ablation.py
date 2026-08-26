import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from context_ablation import (disagreements, fit_offset, market_home_probability,
                              predict)  # noqa: E402
from devig import market_probabilities, multiplicative  # noqa: E402
import line_move  # noqa: E402


def test_offset_model_learns_only_residual_signal():
    x = np.tile([-1.0, 1.0], 100)
    frame = pd.DataFrame({"x": x, "p_market": 0.5,
                          "y": (x > 0).astype(float)})
    beta, transform = fit_offset(frame, ["x"], ridge=0.1)
    probability = predict(frame, ["x"], beta, transform)
    assert probability[x > 0].mean() > 0.9
    assert probability[x < 0].mean() < 0.1


def test_disagreement_reports_same_game_uplift():
    frame = pd.DataFrame({"y": [1.0, 0.0], "p_market": [0.4, 0.6],
                          "o_home": [2.0, 2.0], "o_away": [1.7, 1.7]})
    result = disagreements(frame, np.array([0.6, 0.4]))
    assert result["n"] == 2
    assert result["accuracy_uplift_pp"] == 100.0


def test_experiment_uses_same_shin_probability_as_runtime():
    got = market_home_probability(1.3, 3.6)
    assert got == market_probabilities([1.3, 3.6])[0]
    assert not np.isclose(got, multiplicative([1.3, 3.6])[0])


def test_snapshot_reader_drops_malformed_row_and_accepts_mixed_iso(monkeypatch):
    rows = pd.DataFrame([
        {"ts": "2026-08-01T08:00:00+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A", "away": "B",
         "odds": "1.80,1.80", "result": "경기전"},
        {"ts": "2026-08-01T08:29:59.123456+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A", "away": "B",
         "odds": "1.70,1.90", "result": "경기전"},
        {"ts": "2026-08-01T08:45:00+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A", "away": "B",
         "odds": "1.50,2.20", "result": "1회초"},
        {"ts": "2026-08-01T10:00:00+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A 2", "away": "1 B",
         "odds": "1.40,2.40", "result": "홈승"},
        {"ts": "broken", "year": 2026, "round": 1, "game_no": 11,
         "n_way": "bs", "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "C", "away": "D",
         "odds": "x", "result": "홈승"},
    ])
    monkeypatch.setattr(line_move, "ts_files", lambda: [Path("dummy.csv")])
    monkeypatch.setattr(line_move, "load_timeseries", lambda: rows)
    result = line_move.from_snapshots()
    assert len(result) == 2
    assert set(result["hit"]) == {0, 1}
    assert set(result["odds"]) == {1.7, 1.9}


def test_snapshot_reader_does_not_treat_postgame_price_as_line_move(monkeypatch):
    rows = pd.DataFrame([
        {"ts": "2026-08-01T08:00:00+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A", "away": "B",
         "odds": "1.80,1.80", "result": "경기전"},
        {"ts": "2026-08-01T10:00:00+00:00", "year": 2026, "round": 1,
         "game_no": 10, "n_way": 2, "date_text": "08.01(토) 18:00",
         "league": "KBO", "home": "A 2", "away": "1 B",
         "odds": "1.40,2.40", "result": "홈승"},
    ])
    monkeypatch.setattr(line_move, "ts_files", lambda: [Path("dummy.csv")])
    monkeypatch.setattr(line_move, "load_timeseries", lambda: rows)

    assert line_move.from_snapshots().empty
