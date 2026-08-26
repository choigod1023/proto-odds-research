import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cross_market_edge import prices_at_cutoff  # noqa: E402
from outcome_signal_backtest import proto_prices_at  # noqa: E402
import line_move  # noqa: E402


def snapshot_rows() -> pd.DataFrame:
    kickoff = pd.Timestamp("2026-08-01T10:00:00Z")
    return pd.DataFrame([
        {"market_id": "m1", "event_id": "e1", "kickoff": kickoff,
         "ts": pd.Timestamp("2026-08-01T08:00:00Z"), "odds": "1.80,1.80",
         "n_way": 2, "league": "KBO", "result": "경기전"},
        {"market_id": "m1", "event_id": "e1", "kickoff": kickoff,
         "ts": pd.Timestamp("2026-08-01T09:20:00Z"), "odds": "1.70,1.90",
         "n_way": 2, "league": "KBO", "result": "경기전"},
        # 시각은 kickoff 이전이어도 진행/잠금 상태 가격은 구매 가능으로 보지 않는다.
        {"market_id": "m1", "event_id": "e1", "kickoff": kickoff,
         "ts": pd.Timestamp("2026-08-01T09:29:00Z"), "odds": "1.40,2.40",
         "n_way": 2, "league": "KBO", "result": "1회초"},
        # 빈 값과 '-'는 미정 상태이지 명시적 판매중 증거가 아니다.
        {"market_id": "m1", "event_id": "e1", "kickoff": kickoff,
         "ts": pd.Timestamp("2026-08-01T09:25:00Z"), "odds": "1.50,2.20",
         "n_way": 2, "league": "KBO", "result": ""},
        {"market_id": "m1", "event_id": "e1", "kickoff": kickoff,
         "ts": pd.Timestamp("2026-08-01T09:26:00Z"), "odds": "1.45,2.30",
         "n_way": 2, "league": "KBO", "result": "-"},
    ])


def test_cutoff_readers_require_explicit_pregame_status():
    data = snapshot_rows()
    outcome_settled = pd.DataFrame({"market_id": ["m1"], "winner_idx": [0]})
    cross_settled = pd.DataFrame(
        {"market_id": ["m1"], "winner_idx": [0], "result": ["홈승"]})

    outcome = proto_prices_at(data, outcome_settled, cutoff_min=30)
    cross = prices_at_cutoff(data, cross_settled, cutoff_min=30)

    expected = pd.Timestamp("2026-08-01T09:20:00Z")
    assert outcome.iloc[0]["observed_at"] == expected
    assert cross.iloc[0]["ts"] == expected
    assert outcome.iloc[0]["odds"].tolist() == [1.7, 1.9]
    assert cross.iloc[0]["odds_vec"].tolist() == [1.7, 1.9]


def test_line_move_snapshot_reader_requires_explicit_pregame_status(monkeypatch):
    kickoff = "08.01(토) 19:00"
    common = {
        "year": 2026, "round": 1, "game_no": 1,
        "sport": "bs", "league": "KBO", "market_family": "승패",
        "n_way": 2, "market_label": "", "home": "A", "away": "B",
        "date_text": kickoff,
    }
    data = pd.DataFrame([
        {**common, "ts": "2026-08-01T08:00:00Z", "odds": "1.80,1.80",
         "result": "경기전"},
        {**common, "ts": "2026-08-01T09:20:00Z", "odds": "1.70,1.90",
         "result": "경기전"},
        {**common, "ts": "2026-08-01T09:26:00Z", "odds": "1.45,2.30",
         "result": "-"},
        {**common, "ts": "2026-08-01T10:10:00Z", "odds": "1.70,1.90",
         "result": "홈승", "home": "A 3", "away": "1 B"},
    ])
    monkeypatch.setattr(line_move, "ts_files", lambda: [Path("snapshot.csv")])
    monkeypatch.setattr(line_move, "load_timeseries", lambda: data.copy())

    got = line_move.from_snapshots()

    assert len(got) == 2
    assert set(got["odds"]) == {1.7, 1.9}


def test_line_move_snapshot_reader_rejects_market_identity_change(monkeypatch):
    data = pd.DataFrame([
        {"year": 2026, "round": 1, "game_no": 1, "sport": "bs",
         "league": "KBO", "market_family": "언더오버", "n_way": 2,
         "market_label": "U 7.5", "home": "A", "away": "B",
         "date_text": "08.01(토) 19:00", "ts": "2026-08-01T08:00:00Z",
         "odds": "1.80,1.80", "result": "경기전"},
        {"year": 2026, "round": 1, "game_no": 1, "sport": "bs",
         "league": "KBO", "market_family": "언더오버", "n_way": 2,
         "market_label": "U 7.5", "home": "A", "away": "B",
         "date_text": "08.01(토) 19:00", "ts": "2026-08-01T09:20:00Z",
         "odds": "1.70,1.90", "result": "경기전"},
        {"year": 2026, "round": 1, "game_no": 1, "sport": "bs",
         "league": "KBO", "market_family": "언더오버", "n_way": 2,
         "market_label": "U 8.5", "home": "A 3", "away": "1 B",
         "date_text": "08.01(토) 19:00", "ts": "2026-08-01T10:10:00Z",
         "odds": "1.70,1.90", "result": "홈승"},
    ])
    monkeypatch.setattr(line_move, "ts_files", lambda: [Path("snapshot.csv")])
    monkeypatch.setattr(line_move, "load_timeseries", lambda: data.copy())

    assert line_move.from_snapshots().empty
