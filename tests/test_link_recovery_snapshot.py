import base64
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts.export_link_recovery_snapshot import collect, decode_transport, remote_source
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runtime_db import RuntimeDatabase


def test_export_is_read_only_bounded_and_does_not_read_private_streams(tmp_path):
    path = tmp_path / 'source.sqlite3'
    db = RuntimeDatabase(path)
    db.put_document('kbo_pregame', {'games': []})
    db.put_document('private_wallet', {'private_value': 'never-export'})
    with db.transaction() as c:
        for sequence in range(1, 260):
            c.execute("INSERT INTO prediction_records(ledger_sequence,record_type,identity,record_hash,record_json) "
                      "VALUES (?,'prediction',?,?,?)", (sequence, str(sequence), str(sequence),
                      json.dumps({'ledger_sequence':sequence})))
    with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as c:
        permitted = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        c.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in permitted else sqlite3.SQLITE_DENY)
        snapshot = collect(c, '2026-11-02T00:00:00Z')
    assert [d['name'] for d in snapshot['documents']] == ['kbo_pregame']
    assert len(snapshot['prediction_tail']) == 256
    assert snapshot['prediction_tail'][0]['ledger_sequence'] == 259
    assert snapshot['scope']['complete_ledger'] is False
    assert 'never-export' not in json.dumps(snapshot)
    assert "?mode=ro" in remote_source() and 'RuntimeDatabase' not in remote_source()


def wire(raw, digest=None):
    return '\n'.join(['SSH preamble', 'LINK_SNAPSHOT_BEGIN',
        base64.b64encode(gzip.compress(raw)).decode(),
        'LINK_SNAPSHOT_SHA256 ' + (digest or hashlib.sha256(raw).hexdigest()),
        'LINK_SNAPSHOT_END', 'SSH trailer'])


def test_verified_transport_ignores_ssh_preamble_but_checks_exact_bytes():
    raw = json.dumps({'schema_version':'link-recovery-snapshot-v1','team':'삼성⚾'}, ensure_ascii=False).encode()
    captured, parsed = decode_transport(wire(raw))
    assert captured == raw and parsed['team'] == '삼성⚾'
    with pytest.raises(ValueError, match='hash mismatch'):
        decode_transport(wire(raw, '0' * 64))
    with pytest.raises(ValueError):
        decode_transport(wire(raw).replace('LINK_SNAPSHOT_END', 'incomplete'))


def test_wrong_export_schema_is_rejected():
    with pytest.raises(ValueError, match='schema'):
        decode_transport(wire(b'{"schema_version":"other"}'))
