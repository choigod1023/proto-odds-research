from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtime_db import RuntimeDatabase  # noqa: E402


def odds_row(ts="2026-08-29T01:00:00+00:00", odds="1.80,2.00"):
    return {"ts": ts, "year": 2026, "round": 102, "game_no": "1001",
            "sport": "야구", "league": "KBO", "market_family": "승패",
            "n_way": 2, "market_label": "일반", "home": "홈", "away": "원정",
            "date_text": "08.29", "odds": odds, "result": "경기전"}


def test_odds_are_idempotent_and_latest_value_is_queryable(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    assert db.insert_odds([odds_row()]) == 1
    assert db.insert_odds([odds_row()]) == 0
    assert db.insert_odds([odds_row("2026-08-29T01:05:00+00:00", "1.75,2.05")]) == 1
    latest = db.latest_odds()
    assert len(latest) == 1
    assert latest[0]["odds"] == [1.75, 2.05]


def test_prediction_records_and_artifacts_survive_source_file_removal(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    record = {"ledger_sequence": 1, "record_type": "prediction",
              "snapshot_id": "snap-1", "event_id": "event-1",
              "captured_at": "2026-08-29T01:00:00Z", "record_hash": "abc"}
    assert db.mirror_prediction_records([record]) == 1
    assert db.mirror_prediction_records([record]) == 0
    assert db.prediction_records() == [record]

    db.store_artifact("picks_v2", {"generated_at": "now", "live": []})
    assert db.counts() == {"odds_snapshots": 0, "prediction_records": 1,
                           "artifacts": 1}


def test_unchanged_migration_source_is_skipped(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    assert db.migration_is_current("odds.csv", "10:20") is False
    db.mark_migrated("odds.csv", "10:20", 7)
    assert db.migration_is_current("odds.csv", "10:20") is True
    assert db.migration_is_current("odds.csv", "11:21") is False


def test_prediction_ledger_restores_jsonl_from_database(tmp_path, monkeypatch):
    from prediction_ledger import PredictionLedger

    db_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("PROODD_DB_PATH", str(db_path))
    record = {"ledger_sequence": 1, "record_type": "prediction",
              "snapshot_id": "snap-1", "event_id": "event-1",
              "captured_at": "2026-08-29T01:00:00Z", "record_hash": "abc"}
    RuntimeDatabase(db_path).mirror_prediction_records([record])
    export = tmp_path / "pregame.jsonl"
    PredictionLedger(export)
    assert json.loads(export.read_text(encoding="utf-8")) == record
