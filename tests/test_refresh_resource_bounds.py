"""Resource optimizations must preserve exact fixtures and failure semantics."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runtime_db import RuntimeDatabase
from pregame_player_capture import capture
from src import odds_live


def test_bulk_fixtures_match_single_lookup_and_preserve_ambiguity(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.db")
    fixture = dict(league="NBA", home_team="H", away_team="A", game_id="one",
                   game_datetime="2026-10-10T18:00:00+09:00", source="fixture",
                   updated_at="2026-10-10T07:00:00+00:00")
    other = dict(fixture, home_team="other", game_id="two")
    db.put_document("player_info", {"games": [fixture, other, other, other,
        dict(fixture, home_team="unrelated", padding="x" * 100000)]})
    requests = [("NBA", home, "A", "2026-10-10T09:00:00Z")
                for home in ("H", "other", "missing")]
    batch = db.player_fixtures_document(requests + requests)
    assert len(batch["games"]) == 3
    for league, home, away, kickoff in requests:
        game = dict(league=league, home=home, away=away)
        kwargs = dict(observed_at="2026-10-10T08:00:00Z", kickoff=kickoff)
        assert capture(game, batch, **kwargs) == capture(
            game, db.player_fixture_document(league, home, away, kickoff), **kwargs)
    assert db.player_fixtures_document([]) == {"games": []}
    assert db.player_fixtures_document([("NBA", "H", "A", "2026-10-11T09:00:00Z")]) == {"games": []}


def test_ledger_reader_does_not_fetchall_and_closes_connection(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / "runtime.db")
    closed = []
    class Rows:
        def __iter__(self):
            yield {"record_json": json.dumps({"sequence": 1})}
            yield {"record_json": json.dumps({"sequence": 2})}
        def fetchall(self):
            pytest.fail("serialized full-ledger copy")
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
        def execute(self, *args):
            return Rows()
    monkeypatch.setattr(db, "connect", Connection)
    assert db.prediction_records() == [{"sequence": 1}, {"sequence": 2}]
    assert closed == [True]


@pytest.mark.parametrize("empty", [False, True])
def test_collect_only_never_starts_decision_in_scraper_process(monkeypatch, empty):
    artifact = {"rounds": [117], "n": 1, "generated_at": "2026-10-10T00:00:00Z"}
    monkeypatch.setattr(odds_live, "load_artifact", lambda *a: artifact)
    monkeypatch.setattr(odds_live, "collect", lambda *a: {})
    monkeypatch.setattr(odds_live, "merge_market_history", lambda *a: {} if empty else artifact)
    monkeypatch.setattr(odds_live, "_carry_forward_prices", lambda *a: artifact)
    saved = []
    monkeypatch.setattr(odds_live, "persist_artifact", lambda name, *a, **kw: saved.append(name))
    monkeypatch.setattr(odds_live, "refresh_once", lambda *a: pytest.fail("shared scraper heap"))
    assert odds_live.main(["odds", "--collect-only"]) == 0
    assert saved == ["live_odds"]
