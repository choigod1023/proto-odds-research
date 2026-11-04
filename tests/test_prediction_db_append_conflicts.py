"""Regression tests for competing ledger locks against the same SQLite DB."""
from copy import deepcopy
from datetime import datetime, timezone
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prediction_ledger import PredictionLedger, PredictionLedgerError
from runtime_db import RuntimeDatabase
from tests.test_live_market_refresh import admissible_votes_for_offer_ledger_regressions
from tests.test_official_result_backfill import inputs


CONFLICT = (PredictionLedgerError, ValueError, RuntimeError, sqlite3.IntegrityError)
CLOCK = lambda: datetime(2027, 1, 2, tzinfo=timezone.utc)


def _record(sequence, snapshot, record_hash, previous=None, *, include_previous=True):
    row = {'ledger_sequence': sequence, 'record_type': 'prediction',
           'snapshot_id': snapshot, 'event_id': 'event-' + snapshot,
           'captured_at': '2026-08-29T01:00:00Z', 'record_hash': record_hash}
    if include_previous:
        row['previous_record_hash'] = previous
    return row


def test_different_ledger_locks_reject_stale_batch_without_corrupting_chain(tmp_path, monkeypatch):
    # Seed a real, verified prediction in a temporary file before enabling DB.
    monkeypatch.delenv('PROODD_DB_PATH', raising=False)
    runtime, _, sid = inputs(tmp_path / 'seed')
    initial = runtime.ledger.records()
    monkeypatch.setenv('PROODD_DB_PATH', str(tmp_path / 'runtime.sqlite3'))
    first = PredictionLedger(tmp_path / 'writer-a' / 'pregame.jsonl', clock=CLOCK)
    second = PredictionLedger(tmp_path / 'writer-b' / 'pregame.jsonl', clock=CLOCK)
    assert first.lock_path != second.lock_path
    assert first._database.mirror_prediction_records(initial) == 1

    with first.verified_session():
        assert first.records() == initial
        concurrent = second.append_settlement(
            sid, outcome={'result': 'miss'}, settled_at='2027-01-01T00:00:00Z',
            source={'name': 'other-writer'}, settlement_version='other-v1')
        assert concurrent.appended
        expected = second.records()
        # Two staged rows expose both the ignored first INSERT and any later
        # partially inserted row pointing at that absent hash.
        batch = [dict(snapshot_id=sid, outcome={'result': 'hit'},
                      settled_at='2027-01-01T00:00:00Z', source={'name': 'stale'},
                      settlement_version=version) for version in ('stale-v1', 'stale-v2')]
        with pytest.raises(CONFLICT):
            first.append_settlements(batch)

    # records() verifies sequence, hashes and links as well as stored contents.
    assert second.records() == expected
    assert len(expected) == 2


def test_identical_record_and_batch_replay_are_idempotent(tmp_path):
    db = RuntimeDatabase(tmp_path / 'runtime.sqlite3')
    # Legacy root records without previous_record_hash are an accepted input.
    root = _record(1, 'root', 'hash-root', include_previous=False)
    next_row = _record(2, 'next', 'hash-next', 'hash-root')
    assert db.mirror_prediction_records([root]) == 1
    assert db.mirror_prediction_records([deepcopy(root)]) == 0
    assert db.mirror_prediction_records([deepcopy(root), next_row]) == 1
    assert db.mirror_prediction_records([deepcopy(root), deepcopy(next_row)]) == 0
    assert db.prediction_records() == [root, next_row]


@pytest.mark.parametrize('collision', ['sequence', 'identity', 'hash'])
def test_later_batch_collision_rolls_back_first_new_row(tmp_path, collision):
    db = RuntimeDatabase(tmp_path / 'runtime.sqlite3')
    root = _record(1, 'root', 'hash-root', include_previous=False)
    assert db.mirror_prediction_records([root]) == 1
    first = _record(2, 'new', 'hash-new', 'hash-root')
    second = _record(3, 'other', 'hash-other', 'hash-new')
    if collision == 'sequence':
        second['ledger_sequence'] = 1
    elif collision == 'identity':
        second['snapshot_id'] = 'root'
    else:
        second['record_hash'] = 'hash-root'

    with pytest.raises(CONFLICT):
        db.mirror_prediction_records([first, second])
    assert db.prediction_records() == [root]


@pytest.mark.parametrize('changed', ['event_id', 'record_hash'])
def test_same_sequence_and_identity_with_changed_payload_is_not_replay(tmp_path, changed):
    db = RuntimeDatabase(tmp_path / 'runtime.sqlite3')
    root = _record(1, 'root', 'hash-root', include_previous=False)
    assert db.mirror_prediction_records([root]) == 1
    replacement = {**root, changed: 'changed'}
    with pytest.raises(CONFLICT):
        db.mirror_prediction_records([replacement])
    assert db.prediction_records() == [root]


@pytest.mark.parametrize('problem', ['sequence_gap', 'wrong_previous_hash'])
def test_new_record_must_extend_database_tail(tmp_path, problem):
    db = RuntimeDatabase(tmp_path / 'runtime.sqlite3')
    root = _record(1, 'root', 'hash-root', include_previous=False)
    assert db.mirror_prediction_records([root]) == 1
    new = _record(2, 'new', 'hash-new', 'hash-root')
    if problem == 'sequence_gap':
        new['ledger_sequence'] = 3
    else:
        new['previous_record_hash'] = 'wrong'
    with pytest.raises(CONFLICT):
        db.mirror_prediction_records([new])
    assert db.prediction_records() == [root]
