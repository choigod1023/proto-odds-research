import csv
from datetime import datetime, timezone
import gzip
import json

import pandas as pd
import pytest

from runtime_db import RuntimeDatabase, load_document, persist_frame, read_frame
from export_runtime import export_csv
from migrate_runtime_db import backfill_match_history, migrate_archives, _migrate_runtime_sources


def final(**overrides):
    return {"year": 2026, "round": 105, "game_no": "1", "date_text": "09.04(금) 18:00",
            "sport": "bs", "league": "KBO", "market_family": "승패", "is_void": False,
            "home": "LG 5", "away": "3 삼성", "result": "홈승", **overrides}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("PROODD_DB_PATH", str(path))
    return RuntimeDatabase(path)


def test_frame_is_typed_without_reading_any_csv(db, tmp_path, monkeypatch):
    rows = [{"year": "2026", "odds": "1.55", "is_void": "False", "label": "",
             "date": "2026-09-04", "team": "LG"}]
    db.replace_dataset_rows("test", rows, list(rows[0]))
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("runtime CSV access"))
    frame = read_frame("test", tmp_path / "missing.csv", parse_dates=["date"])
    assert frame.iloc[0]["year"] == 2026
    assert frame.iloc[0]["odds"] == 1.55
    assert frame.iloc[0]["is_void"] == False
    assert pd.isna(frame.iloc[0]["label"])
    assert frame.iloc[0]["date"] == pd.Timestamp("2026-09-04")
    assert list(read_frame("test", tmp_path / "none", usecols=["team"]).columns) == ["team"]
    assert len(list(read_frame("test", tmp_path / "none", chunksize=1))) == 1
    with pytest.raises(KeyError):
        read_frame("missing", tmp_path / "missing.csv")


def test_persist_frame_never_writes_csv(db, tmp_path):
    frame = pd.DataFrame({"x": [1.5, float("nan")], "when": [datetime(2026, 9, 4), pd.NaT]})
    path = tmp_path / "forbidden.csv"
    persist_frame("computed", frame, path)
    assert not path.exists()
    restored = read_frame("computed", path, parse_dates=["when"])
    assert restored.iloc[0]["x"] == 1.5
    assert pd.isna(restored.iloc[1]["when"])


def test_pair_failure_preserves_both_datasets_and_history(db):
    db.replace_dataset_rows("processed_games", [final()], list(final()))
    db.replace_dataset_rows("processed_bets", [{"id": "old"}], ["id"])
    def broken():
        yield {"id": "new"}
        raise ValueError("parse failed")
    with pytest.raises(ValueError):
        db.replace_datasets_rows({"processed_games": ([final(home="LG 7")], list(final())),
                                  "processed_bets": (broken(), ["id"])})
    assert list(db.iter_dataset("processed_bets")) == [{"id": "old"}]
    assert db.match_history()[0]["home_score"] == 5


def test_history_deduplicates_reissues_preserves_doubleheaders_and_excludes_conflicts(db):
    rows = [final(), final(round=106), final(game_no="2", date_text="09.04(금) 20:00")]
    db.replace_dataset_rows("processed_games", rows, list(final()))
    db.record_match_rows([{**row, "ts": "2026-09-04T18:50:00+09:00"} for row in rows])
    assert len(db.match_history()) == 2
    assert len(db.match_history(before="2026-09-04T19:00:00")) == 1
    assert db.match_history(sports=("sc",)) == []
    db.record_match_rows([final(round=106, home="LG 6")])
    assert len(db.match_history()) == 1  # differing physical score rows withheld
    assert db.match_history()[0]["kickoff"].endswith("20:00:00")


def test_history_rejects_adjusted_scores_wrong_result_and_unfinished(db):
    rows = [final(market_family="핸디캡"), final(result="경기전"), final(is_void="True"),
            final(result="홈패"), final(home="LG -1.5"), final(date_text="02.30 18:00")]
    db.record_match_rows(rows)
    assert db.match_history() == []
    db.record_match_rows([final(year=2026, round=1, date_text="12.31 18:00")])
    assert db.match_history()[0]["year"] == 2025


def test_backfill_uses_database_and_preserves_existing_source(db):
    db.replace_dataset_rows("processed_games", [final()], list(final()))
    with db.transaction() as connection:
        connection.execute("DELETE FROM match_results")
    backfill_match_history(db)
    assert db.match_history()[0]["home_team"] == "LG"
    assert db.migration_is_current("match-results-v1", db.dataset_metadata("processed_games")["content_hash"])


def test_bootstrap_does_not_overwrite_existing_dataset(db, tmp_path):
    db.replace_dataset_rows("processed_games", [final()], list(final()))
    source = tmp_path / "data/processed/games.csv"
    source.parent.mkdir(parents=True)
    source.write_text("garbage\nstale\n")
    _migrate_runtime_sources(tmp_path, db)
    assert db.match_history()[0]["home_team"] == "LG"


def test_archives_migrate_once_and_never_replace_new_db_cache(db, tmp_path):
    archive = tmp_path / "data/raw/wisetoto/2026/105.html.gz"
    archive.parent.mkdir(parents=True)
    with gzip.open(archive, "wt", encoding="utf-8") as handle:
        handle.write("old HTML")
    assert migrate_archives(tmp_path, db) == 1
    assert db.get_document("archive:2026:105") == "old HTML"
    db.put_document("archive:2026:105", "latest HTML")
    assert migrate_archives(tmp_path, db) == 0
    assert db.get_document("archive:2026:105") == "latest HTML"


def test_missing_document_does_not_rehydrate_old_export(db, tmp_path):
    fixture = tmp_path / "stale.json"
    fixture.write_text('{"old":true}')
    assert load_document("not-in-db", fixture) is None


def test_explicit_export_is_readonly_no_overwrite_and_spreadsheet_safe(db, tmp_path):
    db.replace_dataset_rows("sample", [{"name": "=HYPERLINK(1)", "value": -2.5}], ["name", "value"])
    before = db.dataset_metadata("sample")
    output = tmp_path / "requested.csv"
    assert not output.exists()
    assert export_csv(db.path, output, kind="dataset", name="sample") == 1
    with output.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row == {"name": "'=HYPERLINK(1)", "value": "-2.5"}
    assert db.dataset_metadata("sample") == before
    with pytest.raises(FileExistsError):
        export_csv(db.path, output, kind="dataset", name="sample")
    with pytest.raises(ValueError):
        export_csv(db.path, db.path, kind="odds", overwrite=True)
    db.put_document("nested", {"games": [{"id": 1}]})
    assert export_csv(db.path, tmp_path / "nested.csv", kind="document", name="nested") == 1


def test_db_push_does_not_export_or_touch_git(db, monkeypatch):
    from deploy import supervisor
    monkeypatch.setattr(supervisor, "_export_site_artifacts", lambda: pytest.fail("automatic export"))
    monkeypatch.setattr(supervisor, "sh", lambda *a, **k: pytest.fail("data git push"))
    supervisor.push_data()


def test_fileless_results_to_forms_to_prediction_ledger_to_api_artifact(db, tmp_path, monkeypatch):
    from live_market_refresh import refresh_document, record_live_market_revisions
    from prediction_runtime import PredictionRuntime
    from runtime_db import load_artifact, persist_artifact
    db.record_match_rows([final(ts="2026-09-04T22:00:00+09:00")])
    now = datetime(2026, 9, 5, 1, tzinfo=timezone.utc)
    feed = {"generated_at": now.isoformat(), "markets": {"105": {"2": {
        "game_no": "2", "date": "09.05(토) 18:00", "sport": "bs", "league": "KBO",
        "home": "LG", "away": "삼성", "market": "승패", "label": "",
        "odds": [1.55, 2.3], "result": "경기전"}}}}
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("CSV dependency"))
    document, changed = refresh_document({"live": [], "past": []}, feed, now=now)
    assert changed == 1
    assert document["live"][0]["form_home"]["w"] == 1
    assert document["live"][0]["form_away"]["l"] == 1
    ledger_path = tmp_path / "forbidden.jsonl"
    runtime = PredictionRuntime(ledger_path, clock=lambda: now)
    counts = record_live_market_revisions(document, now.isoformat(), runtime)
    assert counts["predictions"] == 1
    assert not ledger_path.exists()
    recorded = db.prediction_records()[0]
    assert recorded["features"]["team_context"]["home_form"]["w"] == 1
    output = tmp_path / "forbidden.json"
    persist_artifact("picks_v2", document, output)
    assert not output.exists()
    loaded = load_artifact("picks_v2", output)
    assert loaded["live"][0]["prediction_status"] == "recorded_pregame"
    wire, _ = db.get_artifact_json("picks_v2")
    assert json.loads(wire) == loaded


def test_result_availability_corrections_and_invalidations_are_time_versioned(db):
    db.record_match_rows([final(ts="2026-09-04T22:00:00+09:00")])
    assert db.match_history(before="2026-09-04T19:00:00") == []
    assert db.match_history(before="2026-09-04T23:00:00")[0]["home_score"] == 5
    db.record_match_rows([final(home="LG 6", ts="2026-09-05T01:00:00+09:00")])
    assert db.match_history(before="2026-09-04T23:00:00")[0]["home_score"] == 5
    assert db.match_history(before="2026-09-05T02:00:00")[0]["home_score"] == 6
    db.record_match_rows([final(result="취소", ts="2026-09-05T03:00:00+09:00")])
    assert db.match_history(before="2026-09-05T04:00:00") == []
    assert db.match_history(before="2026-09-05T02:00:00")[0]["home_score"] == 6
    assert db.record_match_rows([final(result="취소", ts="2026-09-05T04:00:00+09:00")]) == 0


def test_dataset_compare_and_swap_prevents_old_games_new_bets_mixture(db):
    old = [{"value": "old"}]
    db.replace_datasets_rows({"g": (old, ["value"]), "b": (old, ["value"])})
    def slow_old_rows():
        yield {"value": "stale"}
        db.replace_datasets_rows({"g": ([{"value": "new"}], ["value"]),
                                  "b": ([{"value": "new"}], ["value"])})
    with pytest.raises(RuntimeError, match="Dataset changed"):
        db.replace_dataset_rows("g", slow_old_rows(), ["value"], expected_revisions={"g": 1})
    assert list(db.iter_dataset("g")) == list(db.iter_dataset("b")) == [{"value": "new"}]


def test_atomic_bootstrap_does_not_clobber_concurrent_winner(db):
    db.put_document("archive:2026:105", "fresh")
    assert db.put_document_if_absent("archive:2026:105", "stale") is False
    assert db.get_document("archive:2026:105") == "fresh"
    def slow_legacy():
        yield {"x": "stale"}
        db.replace_dataset_rows("bootstrap", [{"x": "fresh"}], ["x"])
    db.replace_dataset_rows("bootstrap", slow_legacy(), ["x"], insert_only=True)
    assert list(db.iter_dataset("bootstrap")) == [{"x": "fresh"}]
