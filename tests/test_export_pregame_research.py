from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pregame_research_store import PregameResearchStore
from scripts.export_pregame_research import export


def test_export_pages_pending_without_modifying_database(tmp_path):
    path = tmp_path / "research.db"
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    store = PregameResearchStore(path, official_sources={"proto_official"}, clock=lambda: now)
    for i in range(101):
        store.append_pair(event_id=f"e{i}", kickoff=now+timedelta(hours=3),
                          observed_at=now, source="baseline", snapshot_id=f"s{i}",
                          selection_id="home", odds={"decimal": 1.8},
                          baseline_decision={"probability": .6})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    out = StringIO()
    summary = export(path, out)
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(rows) == summary["pairs"] == 101
    assert summary["complete"] == 0
    assert summary["model_effect_roi"] is None
    assert summary["monthly_loss_limit_krw"] == 50000
    assert all("missing_official_result" in row["incomplete_flags"] for row in rows)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_input_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        export(path, StringIO())
    assert not path.exists()
