import base64
import gzip
import hashlib
import io
import json

import pytest

from scripts import settled_forward_research as cycle
from scripts import sync_operating_ledger as sync


def test_ledger_stream_requires_complete_range_and_hash():
    added = b'{"ledger_sequence":12}\n'
    chunk = base64.b64encode(gzip.compress(added))
    tail = hashlib.sha256(added).hexdigest().encode()
    frames = [b"ledger-start 11 12\n", b"ledger-data " + chunk, b"ledger-end 12 " + tail]
    sink = io.BytesIO()
    assert sync.receive(frames, sink, 11) == (12, len(added))
    assert sink.getvalue() == added
    with pytest.raises(ValueError, match="corrupted"):
        sync.receive(frames[:-1], io.BytesIO(), 11)
    with pytest.raises(ValueError, match="corrupted"):
        sync.receive(frames[:-1] + [b"ledger-end 12 bad"], io.BytesIO(), 11)
    with pytest.raises(ValueError, match="range"):
        sync.receive(frames, io.BytesIO(), 10)


def test_refresh_and_alert_only_on_settlement(monkeypatch, tmp_path):
    candidate = []
    attempts = []
    alerts = []
    monkeypatch.setattr(cycle, "kbo_source", lambda *_: (list(candidate), None))
    monkeypatch.setattr(cycle, "league_source", lambda *_: ([], None))
    monkeypatch.setattr(cycle, "tipster_source", lambda *_: ([], {}))
    monkeypatch.setattr(cycle, "PredictionLedger", lambda _: type("Ledger", (), {"records": lambda _: []})())

    def refresh(*_):
        attempts.append("refresh")
        return {"verified_rows": 0}

    monkeypatch.setattr(sync, "run", refresh)
    monkeypatch.setattr(cycle, "notify", alerts.append)
    cohort = {"KBO": {"settled": 1}}
    monkeypatch.setattr(cycle, "compare", lambda *_: cohort)
    assert cycle.run(tmp_path, tmp_path) == "no_settlement_candidates"
    assert not attempts and not alerts
    candidate.append("first settlement")
    assert cycle.run(tmp_path, tmp_path) == "compared"
    assert attempts == ["refresh"] and alerts == [cohort]
    assert json.loads((tmp_path / "comparison.json").read_bytes())["cohorts"] == cohort
    assert cycle.run(tmp_path, tmp_path) == "compared"
    assert attempts == ["refresh"] and alerts == [cohort]


def test_failed_refresh_does_not_finalize_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(cycle, "kbo_source", lambda *_: (["settlement"], None))
    monkeypatch.setattr(cycle, "league_source", lambda *_: ([], None))
    monkeypatch.setattr(cycle, "tipster_source", lambda *_: ([], {}))

    def fail(*_):
        raise ValueError("invalid ledger chain")

    monkeypatch.setattr(sync, "run", fail)
    with pytest.raises(ValueError, match="invalid ledger chain"):
        cycle.run(tmp_path, tmp_path)
    assert not (tmp_path / "comparison.json").exists()
    assert not (tmp_path / "status.json").exists()
