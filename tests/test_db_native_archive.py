"""Archive producers against a real SQLite DB, with no operational data files."""
from __future__ import annotations

import csv
import gzip
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_dataset
import collect
import snapshot
import wisetoto
from runtime_db import RuntimeDatabase


def html(*, odds="1.80", result="홈승", game_no="001"):
    return f"""<div class="gameinfo"><ul>
        <li class="a1">{game_no}</li><li class="a2">09.01(화) 18:00</li>
        <li class="a3 bs">야구</li><li class="a4">KBO</li><li class="hm">일반</li>
        <li class="a6">LG 3</li><li class="a7">:</li><li class="a8">1 KIA</li>
        <li class="a9">{odds}</li><li class="a9">-</li><li class="a9">2.00</li>
        <li>{result}</li></ul></div>"""


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    cache = tmp_path / "raw"
    out = tmp_path / "processed"
    snapshots = tmp_path / "snapshots"
    monkeypatch.setattr(wisetoto, "CACHE", cache)
    monkeypatch.setattr(build_dataset, "CACHE", cache)
    monkeypatch.setattr(build_dataset, "OUT", out)
    monkeypatch.setattr(snapshot, "OUT", snapshots)
    monkeypatch.setattr(snapshot, "CH_FILE", snapshots / "changes.csv")
    monkeypatch.setattr(snapshot, "LEGACY_TS", snapshots / "odds_timeseries.csv")
    monkeypatch.setattr(wisetoto.time, "sleep", lambda *_: None)
    return RuntimeDatabase()


def no_files():
    assert not wisetoto.CACHE.exists()
    assert not build_dataset.OUT.exists()
    assert not snapshot.OUT.exists()


def session(body):
    response = SimpleNamespace(text=body, content=body.encode(), raise_for_status=lambda: None)
    return SimpleNamespace(get=lambda *args, **kwargs: response)


def fail(*args, **kwargs):
    raise AssertionError("Unexpected filesystem/network/export access")


def test_archive_cache_uses_db_only(database, monkeypatch):
    database.put_document("archive:2026:105", html())
    monkeypatch.setattr(wisetoto, "_cache_path", fail)
    monkeypatch.setattr(wisetoto, "_session", fail)
    assert wisetoto.archive_cached(2026, 105)
    assert wisetoto.fetch_round(2026, 105) == html()
    assert wisetoto.latest_archived_round(2026) == 105
    no_files()


def test_fetch_stores_html_and_updates_history_without_files(database, monkeypatch):
    monkeypatch.setattr(wisetoto, "get_master_seq", lambda *args: "42")
    monkeypatch.setattr(wisetoto, "_cache_path", fail)
    calls = []
    original = RuntimeDatabase.record_match_rows

    def record(db, rows, *, source):
        rows = list(rows)
        calls.append((rows, source))
        return original(db, rows, source=source)

    monkeypatch.setattr(RuntimeDatabase, "record_match_rows", record)
    assert wisetoto.fetch_round(2026, 105, session(html())) == html()
    assert database.get_document("archive:2026:105") == html()
    assert database.document_metadata("archive:2026:105")["generated_at"]
    assert calls[0][1] == "proto"
    assert calls[0][0][0]["home"] == "LG 3"
    assert calls[0][0][0]["market_family"] == "승패"
    assert calls[0][0][0]["is_void"] is False
    history = database.match_history()
    assert len(history) == 1
    assert (history[0]["home_team"], history[0]["home_score"],
            history[0]["away_team"], history[0]["away_score"]) == ("LG", 3, "KIA", 1)
    refreshed = html(odds="1.75")
    assert wisetoto.fetch_round(2026, 105, session(refreshed), use_cache=False) == refreshed
    assert database.get_document("archive:2026:105") == refreshed
    no_files()


def test_collect_counts_db_cache_and_fetches_missing_round(database, monkeypatch):
    database.put_document("archive:2026:1", html())
    monkeypatch.setattr(collect, "MAX_ROUND", 2)
    monkeypatch.setattr(collect, "_session", lambda: session(html()))
    monkeypatch.setattr(wisetoto, "get_master_seq", lambda *args: "42")
    assert collect.collect_year(2026) == (1, 1)
    assert database.get_document("archive:2026:2") == html()
    no_files()


def test_missing_seq_does_not_create_archive(database, monkeypatch):
    monkeypatch.setattr(wisetoto, "get_master_seq", lambda *args: None)
    assert wisetoto.fetch_round(2026, 999, session(html())) is None
    assert not wisetoto.archive_cached(2026, 999)
    no_files()


def test_snapshot_fetch_records_archive_and_matches(database):
    rows = snapshot._fetch(session(html()), 2026, 105, "42")
    assert len(rows) == 1
    assert database.get_document("archive:2026:105") == html()
    assert database.document_metadata("archive:2026:105")["generated_at"]
    assert len(database.match_history()) == 1
    no_files()


def test_snapshots_persist_each_round_without_exports(database, monkeypatch):
    monkeypatch.setattr(snapshot, "_session", object)
    monkeypatch.setattr(RuntimeDatabase, "export_odds_csv", fail)
    monkeypatch.setattr(RuntimeDatabase, "export_events_csv", fail)
    monkeypatch.setattr(RuntimeDatabase, "latest_odds", fail)
    monkeypatch.setattr(snapshot, "_now", lambda: "2026-09-01T10:00:00+00:00")

    def fetch(sess, year, rnd):
        if rnd == 106:
            # The preceding round is already durable before the next fetch.
            assert database.counts()["odds_snapshots"] >= 1
        return wisetoto.parse_rows(html(), year, rnd)

    monkeypatch.setattr(snapshot, "_fetch", fetch)
    assert snapshot.snap(2026, [105, 106]) == 0
    assert len(snapshot._load_last(2026, 105)) == 1
    monkeypatch.setattr(snapshot, "_now", lambda: "2026-09-01T10:01:00+00:00")
    monkeypatch.setattr(snapshot, "_fetch", lambda sess, year, rnd:
                        wisetoto.parse_rows(html(odds="1.75"), year, rnd))
    assert snapshot.snap(2026, [105]) == 1
    assert database.counts()["odds_snapshots"] == 3
    changes = database.events("odds_changes")
    assert len(changes) == 1
    assert changes[0]["prev_odds"] == "1.80,2.00"
    frame = snapshot.load_timeseries()
    assert len(frame) == 3
    assert frame.iloc[-1]["odds"] == "1.75,2.00"
    no_files()


def test_dataset_pair_built_from_db_without_files(database, monkeypatch):
    database.put_document("archive:2025:10", html())
    database.put_document("archive:2026:2", html(result="경기전"))
    database.put_document("archive:2026:10", html(result="취소"))
    monkeypatch.setattr(RuntimeDatabase, "export_dataset_csv", fail)
    assert build_dataset.main() == 0
    games = list(database.iter_dataset("processed_games"))
    bets = list(database.iter_dataset("processed_bets"))
    assert len(games) == 3
    assert [int(row["round"]) for row in games] == [10, 2, 10]
    assert games[0]["odds"] == "1.80,2.00"
    assert len(bets) == 2
    assert [int(row["won"]) for row in bets] == [1, 0]
    assert len(database.match_history()) == 1
    no_files()


def test_no_archives_preserves_existing_dataset_pair(database):
    database.replace_datasets_rows({name: ([{"old": "kept"}], ["old"])
                                    for name in ("processed_games", "processed_bets")})
    assert build_dataset.main() == 1
    for name in ("processed_games", "processed_bets"):
        assert list(database.iter_dataset(name)) == [{"old": "kept"}]
    no_files()


@pytest.mark.parametrize("bad_payload", ["<html>empty newest year</html>", {"wrong": "shape"}])
def test_invalid_latest_archive_preserves_pair(database, bad_payload):
    database.put_document("archive:2025:1", html())
    assert build_dataset.main() == 0
    previous = {name: database.dataset_metadata(name)
                for name in ("processed_games", "processed_bets")}
    database.put_document("archive:2026:1", bad_payload)
    assert build_dataset.main() == 1
    assert {name: database.dataset_metadata(name) for name in previous} == previous
    no_files()


def test_second_dataset_failure_rolls_back_both(database, monkeypatch):
    database.put_document("archive:2026:1", html())
    assert build_dataset.main() == 0
    previous = {name: database.dataset_metadata(name)
                for name in ("processed_games", "processed_bets")}
    database.put_document("archive:2026:2", html())

    def broken_bets(rows):
        raise RuntimeError("bet conversion failed")

    monkeypatch.setattr(build_dataset, "to_bets", broken_bets)
    with pytest.raises(RuntimeError, match="bet conversion failed"):
        build_dataset.main()
    assert {name: database.dataset_metadata(name) for name in previous} == previous
    no_files()


def test_build_uses_one_source_snapshot_and_allows_writes_during_parse(database, monkeypatch):
    database.put_document("archive:2026:1", html())
    original = build_dataset.parse_rows
    observed = []

    def parse(body, year, rnd):
        # Fails immediately if the producer holds the live writer lock while parsing.
        connection = sqlite3.connect(database.path, timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()
        observed.append(body)
        if len(observed) == 1:
            database.put_document("archive:2026:1", html(odds="1.75"))
        return original(body, year, rnd)

    monkeypatch.setattr(build_dataset, "parse_rows", parse)
    assert build_dataset.main() == 0
    assert observed == [html(), html()]
    assert list(database.iter_dataset("processed_games"))[0]["odds"] == "1.80,2.00"
    assert float(list(database.iter_dataset("processed_bets"))[0]["odds"]) == 1.8
    no_files()


def test_archive_generator_does_not_parse_ahead(database, monkeypatch):
    for rnd in range(1, 11):
        database.put_document(f"archive:2026:{rnd}", html())
    original = build_dataset.parse_rows
    parsed = []

    def parse(body, year, rnd):
        parsed.append(rnd)
        return original(body, year, rnd)

    monkeypatch.setattr(build_dataset, "parse_rows", parse)
    connection = database.connect()
    records = build_dataset._database_records(connection, bets=False, stats={})
    try:
        assert next(records)["game_no"] == "001"
        assert parsed == [1]
        assert next(records)["game_no"] == "001"
        assert parsed == [1, 2]
    finally:
        records.close()
        connection.close()


def test_development_gzip_and_csv_fallback(database, monkeypatch):
    monkeypatch.delenv("PROODD_DB_PATH")
    wisetoto.store_archive(2026, 105, html())
    assert wisetoto.archive_cached(2026, 105)
    with gzip.open(wisetoto._cache_path(2026, 105), "rt", encoding="utf-8") as handle:
        assert handle.read() == html()
    monkeypatch.setattr(wisetoto, "_session", fail)
    assert wisetoto.fetch_round(2026, 105) == html()
    assert build_dataset.main() == 0
    with (build_dataset.OUT / "games.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["odds"] == "1.80,2.00"
    with (build_dataset.OUT / "bets.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_db_missing_archive_never_reads_stale_gzip_fixture(database, monkeypatch):
    path = wisetoto._cache_path(2026, 105)
    path.parent.mkdir(parents=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("stale fixture")
    monkeypatch.setattr(wisetoto, "get_master_seq", lambda *args: "42")
    assert not wisetoto.archive_cached(2026, 105)
    assert wisetoto.latest_archived_round(2026) is None
    assert wisetoto.fetch_round(2026, 105, session(html())) == html()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert handle.read() == "stale fixture"
    assert not build_dataset.OUT.exists()
    assert not snapshot.OUT.exists()
