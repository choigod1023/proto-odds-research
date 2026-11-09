import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixture_history import TIMELINE_FIELDS, encode
from runtime_db import RuntimeDatabase


def test_timeline_identity_and_snapshot_retention(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    row = {"source": "naver", "game_id": "one", "start": "2026-11-09T10:00:00+00:00",
           "md": "11.09", "home": "홈", "away": "원정", "timeline": [{"text": "골"}]}
    next_row = {**row, "game_id": "two", "start": "2026-11-09T12:00:00+00:00",
                "timeline": [{"text": "교체"}]}
    key = lambda game: json.dumps([game.get(field) for field in TIMELINE_FIELDS])
    assert db.get_fixture_history("live_scores", key(row)) is None
    db.store_artifact("live_scores", {"generated_at": "first", "games": [row, next_row]})
    assert json.loads(db.get_fixture_history("live_scores", key(row))) == row
    assert json.loads(db.get_fixture_history("live_scores", key(next_row))) == next_row
    assert db.get_artifact("live_scores_summary")["games"][0].get("timeline") is None
    db.store_artifact("live_scores", {"generated_at": "second", "games": [next_row]})
    assert db.get_fixture_history("live_scores", key(row)) == b"null"
    assert json.loads(db.get_fixture_history("live_scores", key(next_row))) == next_row
    with pytest.raises(ValueError, match="invalid fixture key"):
        db.get_fixture_history("live_scores", '["too short"]')


def test_odds_history_isolated_by_round_and_scheduled_date(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    a = {"home": "홈", "away": "원정", "date": "11.09 20:00", "game_no": "20"}
    b = {**a, "date": "11.09 22:00", "game_no": "2"}
    c = {**a, "game_no": "1"}
    round_markets = {"20": a, "2": b, "1": c}
    markets = {"132": round_markets, "133": {"20": a}}
    entries = {"20": [{"odds": [2, 3]}], "2": [{"odds": [4, 5]}], "1": [{"odds": [6, 7]}]}
    history = {"132": entries, "133": {"20": [{"odds": [8, 9]}]}}
    key = lambda number, game: encode([number, game["home"], game["away"], game["date"]])
    db.store_artifact("live_odds", {"markets": markets, "history": history})
    assert json.loads(db.get_fixture_history("live_odds", key("132", a))) == [
        {"gameNo": "1", "odds": [6, 7]}, {"gameNo": "20", "odds": [2, 3]}]
    assert json.loads(db.get_fixture_history("live_odds", key("132", b))) == [
        {"gameNo": "2", "odds": [4, 5]}]
    assert json.loads(db.get_fixture_history("live_odds", key("133", a))) == [
        {"gameNo": "20", "odds": [8, 9]}]
    assert db.get_fixture_history("live_odds", key("134", a)) == b"[]"
    db.store_artifact("live_odds", {"markets": {}, "history": {}})
    assert db.get_fixture_history("live_odds", key("132", a)) == b"[]"
