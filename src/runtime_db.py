"""Persistent SQLite store for collector data on the Fly volume.

JSON/CSV files remain compatibility exports.  The database lives outside the
git checkout in production, so repository synchronisation cannot delete it.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "runtime" / "proodd.sqlite3"


def configured_path() -> Path:
    return Path(os.environ.get("PROODD_DB_PATH", DEFAULT_PATH))


class RuntimeDatabase:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else configured_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    game_no TEXT NOT NULL,
                    sport TEXT,
                    league TEXT,
                    market_family TEXT,
                    n_way INTEGER,
                    market_label TEXT,
                    home TEXT,
                    away TEXT,
                    date_text TEXT,
                    odds_json TEXT NOT NULL,
                    result TEXT,
                    UNIQUE(observed_at, season, round, game_no)
                );
                CREATE INDEX IF NOT EXISTS idx_odds_game_time
                    ON odds_snapshots(season, round, game_no, observed_at);
                CREATE INDEX IF NOT EXISTS idx_odds_time
                    ON odds_snapshots(observed_at);

                CREATE TABLE IF NOT EXISTS prediction_records (
                    ledger_sequence INTEGER PRIMARY KEY,
                    record_type TEXT NOT NULL,
                    identity TEXT NOT NULL UNIQUE,
                    event_id TEXT,
                    snapshot_id TEXT,
                    captured_at TEXT,
                    record_hash TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prediction_event
                    ON prediction_records(event_id, ledger_sequence);

                CREATE TABLE IF NOT EXISTS artifacts (
                    name TEXT PRIMARY KEY,
                    generated_at TEXT,
                    payload_json TEXT NOT NULL,
                    stored_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS migration_state (
                    source TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL
                );
                """
            )

    def insert_odds(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [(
            str(row["ts"]), int(row["year"]), int(row["round"]), str(row["game_no"]),
            row.get("sport"), row.get("league"), row.get("market_family"),
            int(row["n_way"]) if row.get("n_way") not in (None, "") else None,
            row.get("market_label"), row.get("home"), row.get("away"),
            row.get("date_text"), json.dumps(
                [float(value) for value in str(row["odds"]).split(",") if value],
                separators=(",", ":"),
            ), row.get("result"),
        ) for row in rows]
        if not values:
            return 0
        with self.transaction() as connection:
            before = connection.total_changes
            connection.executemany(
                """INSERT OR IGNORE INTO odds_snapshots
                   (observed_at,season,round,game_no,sport,league,market_family,n_way,
                    market_label,home,away,date_text,odds_json,result)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            return connection.total_changes - before

    def latest_odds(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT o.* FROM odds_snapshots o
                   JOIN (
                     SELECT season,round,game_no,MAX(observed_at) observed_at
                     FROM odds_snapshots GROUP BY season,round,game_no
                   ) latest USING (season,round,game_no,observed_at)
                   ORDER BY o.round,o.game_no"""
            ).fetchall()
        return [{**dict(row), "odds": json.loads(row["odds_json"])} for row in rows]

    def mirror_prediction_records(self, records: Iterable[Mapping[str, Any]]) -> int:
        values = []
        for record in records:
            kind = str(record["record_type"])
            identity = (record.get("snapshot_id") if kind == "prediction"
                        else record.get("settlement_id"))
            values.append((
                int(record["ledger_sequence"]), kind, str(identity),
                record.get("event_id"), record.get("snapshot_id"),
                record.get("captured_at"), str(record["record_hash"]),
                json.dumps(record, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False),
            ))
        if not values:
            return 0
        with self.transaction() as connection:
            before = connection.total_changes
            connection.executemany(
                """INSERT OR IGNORE INTO prediction_records
                   (ledger_sequence,record_type,identity,event_id,snapshot_id,
                    captured_at,record_hash,record_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                values,
            )
            return connection.total_changes - before

    def prediction_records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM prediction_records ORDER BY ledger_sequence"
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def store_artifact(self, name: str, payload: Mapping[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO artifacts(name,generated_at,payload_json,stored_at)
                   VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                   generated_at=excluded.generated_at,
                   payload_json=excluded.payload_json,stored_at=excluded.stored_at""",
                (name, payload.get("generated_at"), body, now),
            )

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in ("odds_snapshots", "prediction_records", "artifacts")
            }

