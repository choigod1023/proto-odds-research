import json
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import runtime_db
from runtime_db import RuntimeDatabase


def _put_raw(db, name, body):
    with db.transaction() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO artifacts(name, payload_json, stored_at) VALUES (?, ?, ?)",
            (name, body, "revision"),
        )


@pytest.mark.parametrize("body", [
    '{"live":[{"emoji":"⚽😀","ko":"한글", "x":123456789012345678901234567890, '
    '"float":1.2345678901234567,"bool":true,"nothing":null}],"dup":1,"dup":2}',
    '[123456789012345678901234567890, 1.5, -0.0, 1e400, 1e-4000, false, null]',
    '[1e999999999999999999999, -1e-999999999999999999999]',
    '"\\ud83d\\ude00"',
    '42',
    'null',
])
def test_stream_matches_standard_json(tmp_path, body):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    _put_raw(db, "picks_v2", body)
    actual = db.get_artifact("picks_v2")
    expected = json.loads(body)
    assert actual == expected
    if isinstance(actual, list) and len(actual) > 2:
        assert type(actual[0]) is int
        assert type(actual[1]) is float
        assert math.copysign(1, actual[2]) == -1
        assert math.isinf(actual[3])


@pytest.mark.parametrize("body", ["", "{", '{"a":1}x', '{"a":1} {"b":2}', '[1,]', '+1', '[+1]'])
def test_stream_rejects_incomplete_or_trailing_json(tmp_path, body):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    _put_raw(db, "picks_v2", body)
    with pytest.raises(ValueError):
        db.get_artifact("picks_v2")


def test_missing_and_other_artifacts(tmp_path):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    assert db.get_artifact("picks_v2") is None
    _put_raw(db, "other", '{"a": 5}')
    assert db.get_artifact("other") == {"a": 5}


def test_concurrent_replacement_keeps_one_snapshot(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    _put_raw(db, "picks_v2", '{"revision":"old","live":[1,2]}')
    original_parse = runtime_db._strict_json_events

    def replace_then_read(blob):
        _put_raw(db, "picks_v2", '{"revision":"new","live":[3,4,5]}')
        yield from original_parse(blob)

    with monkeypatch.context() as patch:
        patch.setattr(runtime_db, "_strict_json_events", replace_then_read)
        assert db.get_artifact("picks_v2") == {"revision": "old", "live": [1, 2]}
    assert db.get_artifact("picks_v2") == {"revision": "new", "live": [3, 4, 5]}


def test_ledger_iteration_preserves_sequence_and_all_rows(tmp_path):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    with db.transaction() as connection:
        for sequence in (3, 1, 2):
            connection.execute(
                "INSERT INTO prediction_records(ledger_sequence,record_type,identity,record_hash,record_json) "
                "VALUES (?, 'prediction', ?, ?, ?)",
                (sequence, str(sequence), str(sequence), json.dumps({"sequence": sequence})),
            )
    assert db.prediction_records() == [{"sequence": 1}, {"sequence": 2}, {"sequence": 3}]
