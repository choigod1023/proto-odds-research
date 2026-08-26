import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from outcome_signal_backtest import (  # noqa: E402
    external_gap_bets,
    settled_markets as outcome_settled_markets,
)
from outcome_signal_backtest_v2 import coverage_at  # noqa: E402
from cross_market_edge import settled_markets as cross_settled_markets  # noqa: E402
import cross_market_edge as cross_module  # noqa: E402
import outcome_signal_backtest as outcome_module  # noqa: E402


def test_external_gap_join_does_not_mix_two_way_and_three_way_markets():
    kickoff = pd.Timestamp("2026-08-01T10:00:00Z")
    common = {
        "event_id": "same-event",
        "kickoff": kickoff,
        "league": "KBO",
        "observed_at": pd.Timestamp("2026-08-01T09:30:00Z"),
    }
    proto = pd.DataFrame([
        {**common, "market_id": "two-way", "market_family": "승패",
         "n_way": 2, "winner_idx": 0, "odds": np.array([1.8, 2.0]),
         "p_proto": np.array([0.53, 0.47])},
        {**common, "market_id": "three-way", "market_family": "승무패",
         "n_way": 3, "winner_idx": 0, "odds": np.array([1.7, 7.0, 3.0]),
         "p_proto": np.array([0.58, 0.14, 0.28])},
    ])
    overseas = pd.DataFrame([
        {**common, "market_family": "승패", "n_way": 2,
         "p_os": np.array([0.60, 0.40])},
    ])

    bets = external_gap_bets(proto, overseas, cutoff_min=30, min_ev=0.0)

    assert bets["market_id"].tolist() == ["two-way"]
    assert coverage_at(proto, overseas, cutoff_min=30) == {
        "overseas_events_at_cutoff": 1,
        "joined_events": 1,
    }


def test_external_gap_join_fails_closed_on_incompatible_market_family():
    kickoff = pd.Timestamp("2026-08-01T10:00:00Z")
    proto = pd.DataFrame([{
        "event_id": "event", "market_id": "bad-proto",
        "kickoff": kickoff, "league": "KBO",
        "observed_at": pd.Timestamp("2026-08-01T09:30:00Z"),
        "market_family": "승무패", "n_way": 2, "winner_idx": 0,
        "odds": np.array([1.8, 2.0]), "p_proto": np.array([0.53, 0.47]),
    }])
    overseas = pd.DataFrame([{
        "event_id": "event", "kickoff": kickoff, "league": "KBO",
        "observed_at": pd.Timestamp("2026-08-01T09:30:00Z"),
        "market_family": "승패", "n_way": 2,
        "p_os": np.array([0.60, 0.40]),
    }])

    assert external_gap_bets(proto, overseas, 30, 0.0).empty
    assert coverage_at(proto, overseas, 30)["joined_events"] == 0


def test_settlement_rejects_market_signature_change_even_when_winner_is_same():
    rows = pd.DataFrame([
        {"market_id": "market", "event_id": "event", "market_signature": "언더오버|2|U 7.5",
         "n_way": 2, "result": "홈승", "home": "A", "away": "B",
         "kickoff": pd.Timestamp("2026-08-01T10:00:00Z"), "sport": "bs",
         "league": "KBO", "market_family": "언더오버", "home_team": "A",
         "away_team": "B"},
        {"market_id": "market", "event_id": "event", "market_signature": "언더오버|2|U 8.5",
         "n_way": 2, "result": "홈승", "home": "A 3", "away": "1 B",
         "kickoff": pd.Timestamp("2026-08-01T10:00:00Z"), "sport": "bs",
         "league": "KBO", "market_family": "언더오버", "home_team": "A",
         "away_team": "B"},
    ])

    assert outcome_settled_markets(rows).empty
    assert cross_settled_markets(rows).empty


def test_snapshot_loaders_restore_round_one_december_to_previous_year(
        tmp_path, monkeypatch):
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    pd.DataFrame([{
        "ts": "2025-12-31T12:00:00Z", "year": 2026, "round": 1,
        "game_no": 1, "sport": "bs", "league": "KBO",
        "market_family": "승패", "n_way": 2, "market_label": "",
        "home": "A", "away": "B", "date_text": "12.31(수) 21:30",
        "odds": "1.80,1.80", "result": "경기전",
    }]).to_csv(snapshot_dir / "odds_timeseries_20251231.csv", index=False)
    monkeypatch.setattr(outcome_module, "SNAP_DIR", snapshot_dir)
    monkeypatch.setattr(cross_module, "SNAP_DIR", snapshot_dir)

    outcome, _ = outcome_module.load_snapshots()
    cross, _ = cross_module.load_snapshots()

    expected = pd.Timestamp("2025-12-31T12:30:00Z")
    assert outcome.iloc[0]["kickoff"] == expected
    assert cross.iloc[0]["kickoff"] == expected
