import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import migrate_runtime_db as migration  # noqa: E402
from runtime_db import RuntimeDatabase  # noqa: E402


def test_player_name_translation_cache_is_a_boot_migration_source():
    assert migration.DOCUMENT_SOURCES["player_names_ko"] == (
        "data/raw/llm_cache/player_names_ko.json"
    )


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


def test_boot_migration_never_replaces_newer_database_artifact(tmp_path):
    artifact_dir = tmp_path / "docs" / "data"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "today.json").write_text(json.dumps({
        "generated_at": "2026-09-01T01:00:00Z", "source": "git",
    }), encoding="utf-8")
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    db.store_artifact("today", {
        "generated_at": "2026-09-01T02:00:00+00:00", "source": "database",
    })

    result = migration.migrate(
        tmp_path, db, include_odds=False, include_runtime_sources=False,
        include_datasets=False,
    )

    assert result["artifacts_imported"] == 0
    assert db.get_artifact("today")["source"] == "database"


def test_boot_migration_imports_only_provably_newer_git_artifact(tmp_path):
    artifact_dir = tmp_path / "docs" / "data"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "today.json").write_text(json.dumps({
        "generated_at": "2026-09-01T03:00:00Z", "source": "git",
    }), encoding="utf-8")
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    db.store_artifact("today", {
        "generated_at": "2026-09-01T02:00:00Z", "source": "database",
    })

    result = migration.migrate(
        tmp_path, db, include_odds=False, include_runtime_sources=False,
        include_datasets=False,
    )

    assert result["artifacts_imported"] == 1
    assert db.get_artifact("today")["source"] == "git"


def test_boot_migration_preserves_database_translation_cache_without_timestamp(
        tmp_path, monkeypatch):
    cache = tmp_path / "player_names_ko.json"
    cache.write_text(json.dumps({"Shohei Ohtani": "옛 파일"}), encoding="utf-8")
    monkeypatch.setattr(migration, "DOCUMENT_SOURCES", {
        "player_names_ko": "player_names_ko.json",
    })
    monkeypatch.setattr(migration, "EVENT_SOURCES", {})
    monkeypatch.setattr(migration, "CSV_EVENT_SOURCES", {})
    monkeypatch.setattr(migration, "DATASET_SOURCES", {})
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    db.put_document("player_names_ko", {"Shohei Ohtani": "오타니 쇼헤이"})

    documents, events, datasets = migration._migrate_runtime_sources(tmp_path, db)

    assert (documents, events, datasets) == (0, 0, 0)
    assert db.get_document("player_names_ko") == {"Shohei Ohtani": "오타니 쇼헤이"}
