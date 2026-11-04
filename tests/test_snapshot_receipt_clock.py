import csv
from types import SimpleNamespace

from src import snapshot
from runtime_db import RuntimeDatabase


def setup(monkeypatch, tmp_path, production):
    clock = ["2026-09-01T10:00:00+00:00"]
    monkeypatch.setattr(snapshot, "_now", lambda: clock[0])
    monkeypatch.setattr(snapshot, "_session", lambda: object())
    monkeypatch.setattr(snapshot, "_load_last", lambda *a: {})
    monkeypatch.setattr(snapshot.time, "sleep", lambda *a: None)
    monkeypatch.setattr(snapshot, "database_enabled", lambda: production)
    monkeypatch.setattr(snapshot, "OUT", tmp_path)
    monkeypatch.setattr(snapshot, "ts_file", lambda *a: tmp_path / "prices.csv")
    monkeypatch.setattr(snapshot, "CH_FILE", tmp_path / "changes.csv")
    db = RuntimeDatabase(tmp_path / "state.sqlite3") if production else None
    if db:
        monkeypatch.setattr(snapshot, "RuntimeDatabase", lambda: db)

    def fetch(session, year, round_no):
        clock[0] = "2026-09-01T10:0" + str(round_no-100) + ":00+00:00"
        return [SimpleNamespace(game_no="1", sport="bs", league="MLB", market_family="승패", n_way=2,
                                market_label="", home="홈팀", away="원정팀", date_text="09.01(화) 20:00",
                                odds=[1.5, 2.5], result="경기전")]

    monkeypatch.setattr(snapshot, "_fetch", fetch)
    return db


def test_each_round_csv_uses_completed_fetch_clock(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, False)
    assert snapshot.snap(2026, [101, 102]) == 0
    with (tmp_path / "prices.csv").open(encoding="utf8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["ts"] for r in rows] == ["2026-09-01T10:01:00+00:00", "2026-09-01T10:02:00+00:00"]


def test_production_receipt_and_quote_share_completed_clock(monkeypatch, tmp_path):
    db = setup(monkeypatch, tmp_path, True)
    assert snapshot.snap(2026, [101, 102]) == 0
    with db.connect() as connection:
        prices = connection.execute("select round,observed_at from odds_snapshots order by round").fetchall()
    receipts = list(db.iter_events("odds_snapshot_receipts"))
    assert len(receipts) == 2
    for row, receipt in zip(prices, receipts):
        assert receipt["round"] == row["round"]
        assert receipt["fetch_completed_at"] == row["observed_at"] == receipt["observed_at"]
        assert receipt["fetch_started_at"] < receipt["fetch_completed_at"]
        assert receipt["timestamp_basis"] == "fetch_parse_completed_upper_bound"
        assert receipt["priced_rows"] == 1 and len(receipt["batch_sha256"]) == 64
