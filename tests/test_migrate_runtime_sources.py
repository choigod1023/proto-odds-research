import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import migrate_runtime_db as migration  # noqa: E402
from runtime_db import RuntimeDatabase  # noqa: E402


def test_boot_migration_imports_events_before_collectors_but_defers_datasets(
        tmp_path, monkeypatch):
    event_path = tmp_path / "legacy.jsonl"
    event_path.write_text(json.dumps({"observed_at": "2026-01-01", "value": 1}) + "\n")
    dataset_path = tmp_path / "large.csv"
    dataset_path.write_text("id,value\n1,x\n", encoding="utf-8")
    monkeypatch.setattr(migration, "DOCUMENT_SOURCES", {})
    monkeypatch.setattr(migration, "CSV_EVENT_SOURCES", {})
    monkeypatch.setattr(migration, "EVENT_SOURCES", {"legacy": "legacy.jsonl"})
    monkeypatch.setattr(migration, "DATASET_SOURCES", {"large": "large.csv"})
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")

    documents, events, datasets = migration._migrate_runtime_sources(
        tmp_path, db, include_datasets=False)

    assert (documents, events, datasets) == (0, 1, 0)
    assert db.events("legacy") == [{"observed_at": "2026-01-01", "value": 1}]
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_revisions").fetchone()[0] == 0
