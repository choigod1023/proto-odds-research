from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtime_db import (RuntimeDatabase, export_site_artifacts,  # noqa: E402
                        persist_artifact, persist_document)


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


def test_malformed_legacy_odds_row_does_not_abort_batch(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    malformed = {**odds_row(), "game_no": "bad", "n_way": "bs"}
    assert db.insert_odds([malformed, odds_row()]) == 1
    assert db.counts()["odds_snapshots"] == 1


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


def test_prediction_ledger_does_not_export_database_to_jsonl(tmp_path, monkeypatch):
    from prediction_ledger import PredictionLedger

    db_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("PROODD_DB_PATH", str(db_path))
    record = {"ledger_sequence": 1, "record_type": "prediction",
              "snapshot_id": "snap-1", "event_id": "event-1",
              "captured_at": "2026-08-29T01:00:00Z", "record_hash": "abc"}
    RuntimeDatabase(db_path).mirror_prediction_records([record])
    export = tmp_path / "pregame.jsonl"
    PredictionLedger(export)
    assert not export.exists()
    assert RuntimeDatabase(db_path).prediction_records() == [record]


def test_documents_are_revisioned_and_exported_from_database(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    payload = {"generated_at": "2026-08-30T01:00:00Z", "games": [{"id": 1}]}
    assert db.put_document("player_info", payload) == 1
    assert db.put_document("player_info", payload) == 1
    changed = {**payload, "games": [{"id": 1}, {"id": 2}]}
    assert db.put_document("player_info", changed) == 2
    assert db.get_document("player_info") == changed
    assert db.document_metadata("player_info")["revision"] == 2

    export = tmp_path / "player_info.json"
    db.export_document("player_info", export)
    assert json.loads(export.read_text(encoding="utf-8")) == changed


def test_document_can_store_top_level_json_array(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    payload = [{"team": "KIA"}, {"team": "LG"}]

    assert db.put_document("starters", payload) == 1
    assert db.get_document("starters") == payload


def test_raw_json_document_is_stored_without_object_assumption(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    raw = '[{"team":"KIA"},{"team":"LG"}]\n'

    assert db.put_document_json("raw_starters", raw) == 1
    assert db.get_document("raw_starters") == [{"team": "KIA"}, {"team": "LG"}]


def test_persist_helpers_use_database_without_runtime_file_exports(tmp_path, monkeypatch):
    database_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("PROODD_DB_PATH", str(database_path))
    document_path = tmp_path / "document.json"
    artifact_path = tmp_path / "artifact.json"

    persist_document("features", {"games": [1]}, document_path)
    persist_artifact("today", {"generated_at": "now", "games": [2]}, artifact_path)
    assert not document_path.exists()
    assert not artifact_path.exists()
    database = RuntimeDatabase(database_path)
    assert database.get_document("features") == {"games": [1]}
    assert database.get_artifact("today") == {"generated_at": "now", "games": [2]}

    # 정적 사이트가 읽는 산출물은 명시적 내보내기 단계로 docs/data 에 되살린다.
    site_dir = tmp_path / "docs" / "data"
    site_dir.mkdir(parents=True)
    database.store_artifact("picks_v2", {"generated_at": "now", "rounds": [104]})
    database.store_artifact("live_odds", {"generated_at": "now", "n": 0})
    written = export_site_artifacts(tmp_path)
    assert set(written) >= {"today", "picks_v2", "live_odds"}
    assert json.loads((site_dir / "picks_v2.json").read_text(encoding="utf-8"))["rounds"] == [104]
    assert (site_dir / "today.json").exists()
    # DB 에 없는 이름은 파일을 만들지 않는다.
    assert not (site_dir / "combo.json").exists()

    # Explicit offline exports remain available for diagnostics/migrations.
    database.export_document("features", document_path)
    database.export_artifact("today", artifact_path)

    assert json.loads(document_path.read_text()) == {"games": [1]}
    assert json.loads(artifact_path.read_text()) == {"generated_at": "now", "games": [2]}


def test_artifact_wire_payload_can_be_served_without_decoding(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    payload = {"generated_at": "now", "games": [{"home": "서울", "away": "부산"}]}
    db.store_artifact("picks_v2", payload)

    raw, revision = db.get_artifact_json("picks_v2")

    assert json.loads(raw) == payload
    assert revision
    assert db.get_artifact_json("missing") is None


def test_event_stream_is_idempotent_and_rebuilds_jsonl(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    rows = [
        {"observed_at": "2026-08-30T01:00:00Z", "event_id": "a", "value": 1},
        {"observed_at": "2026-08-30T02:00:00Z", "event_id": "b", "value": 2},
    ]
    assert db.append_events(
        "weather", rows, identity_keys=("observed_at", "event_id")) == 2
    assert db.append_events(
        "weather", rows, identity_keys=("observed_at", "event_id")) == 0
    assert db.events("weather", through="2026-08-30T01:30:00Z") == rows[:1]

    export = tmp_path / "weather.jsonl"
    db.export_events("weather", export)
    assert [json.loads(line) for line in export.read_text().splitlines()] == rows


def test_event_stream_normalizes_extra_legacy_csv_columns(tmp_path):
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")

    assert db.append_events("legacy_csv", [{"id": "1", None: ["extra"]}]) == 1
    assert db.events("legacy_csv") == [{"id": "1", "_extra": ["extra"]}]


def test_dataset_csv_round_trip(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("a,b\n1,한글\n2,x\n", encoding="utf-8")
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")

    assert db.replace_dataset_csv("sample", source) == 1
    assert db.replace_dataset_csv("sample", source) == 1
    target = tmp_path / "export.csv"
    db.export_dataset_csv("sample", target)

    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
