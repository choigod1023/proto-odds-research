"""Persistent SQLite store for collector data on the Fly volume.

When ``PROODD_DB_PATH`` is set, runtime state is read and written only through
SQLite.  Repository files are development fixtures, never production state.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping
from runtime_datasets import DatasetStore, RESULT_SCHEMA


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "runtime" / "proodd.sqlite3"


def configured_path() -> Path:
    return Path(os.environ.get("PROODD_DB_PATH", DEFAULT_PATH))


def database_enabled() -> bool:
    """운영 DB가 명시된 환경인지 확인한다. 테스트/일회 분석은 파일만 써도 된다."""
    return bool(os.environ.get("PROODD_DB_PATH"))


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class RuntimeDatabase(DatasetStore):
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else configured_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, factory=_Connection)
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
            connection.executescript(RESULT_SCHEMA)
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
                CREATE TABLE IF NOT EXISTS documents (
                    name TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    generated_at TEXT,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    stored_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_records (
                    id INTEGER PRIMARY KEY,
                    stream TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    observed_at TEXT,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    inserted_at TEXT NOT NULL,
                    UNIQUE(stream, identity)
                );
                CREATE INDEX IF NOT EXISTS idx_event_stream_time
                    ON event_records(stream, observed_at, id);
                CREATE TABLE IF NOT EXISTS dataset_revisions (
                    name TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    fieldnames_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_rows (
                    dataset TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(dataset, ordinal),
                    FOREIGN KEY(dataset) REFERENCES dataset_revisions(name)
                        ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    @classmethod
    def _hash(cls, value: Any) -> str:
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    def put_document(self, name: str, payload: Any,
                     *, generated_at: str | None = None) -> int:
        """최신 상태 문서를 저장하고 실제 내용이 바뀐 경우에만 revision을 올린다."""
        body = self._canonical(payload)
        embedded_stamp = None
        if isinstance(payload, Mapping):
            embedded_stamp = payload.get("generated_at") or payload.get("updated_at")
        return self.put_document_json(
            name, body, generated_at=generated_at or embedded_stamp)

    def put_document_json(self, name: str, payload_json: str,
                          *, generated_at: str | None = None) -> int:
        """JSON 원문을 객체로 펼치지 않고 저장한다.

        대형 레거시 배열을 json.loads 후 재직렬화하면 실제 파일의 수십 배 메모리를
        사용한다. 이관 경로는 이미 수집기가 만든 JSON 파일을 그대로 보존한다.
        """
        body = payload_json.strip()
        if not body:
            raise ValueError(f"empty JSON document: {name}")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT revision,content_hash FROM documents WHERE name=?", (name,)
            ).fetchone()
            if current is not None and current["content_hash"] == digest:
                return int(current["revision"])
            revision = 1 if current is None else int(current["revision"]) + 1
            connection.execute(
                """INSERT INTO documents
                   (name,revision,generated_at,content_hash,payload_json,stored_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                   revision=excluded.revision,generated_at=excluded.generated_at,
                   content_hash=excluded.content_hash,payload_json=excluded.payload_json,
                   stored_at=excluded.stored_at""",
                (name, revision, generated_at, digest, body, now),
            )
        return revision

    def get_document(self, name: str) -> Any | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM documents WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def document_metadata(self, name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT name,revision,generated_at,content_hash,stored_at,
                          length(payload_json) payload_bytes
                   FROM documents WHERE name=?""", (name,)
            ).fetchone()
        return dict(row) if row is not None else None

    def append_events(self, stream: str, rows: Iterable[Mapping[str, Any]],
                      *, identity_keys: tuple[str, ...] = (),
                      observed_at_key: str = "observed_at") -> int:
        """append-only 원장에 이벤트를 중복 없이 기록한다.

        identity_keys가 없으면 행 전체 해시가 identity다. 관측시각과 원천 ID를
        함께 넘기면 같은 내용을 다시 수집해도 별도 revision으로 보존할 수 있다.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = []
        for row in rows:
            # 손상된 옛 CSV는 헤더보다 열이 많아 DictReader가 None 키를 만든다.
            # JSON 객체 키로 정규화해 한 행 때문에 전체 원장을 중단하지 않는다.
            payload = {(str(key) if key is not None else "_extra"): value
                       for key, value in dict(row).items()}
            body = self._canonical(payload)
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            identity_value = ([payload.get(key) for key in identity_keys]
                              if identity_keys else digest)
            identity = self._hash(identity_value)
            values.append((stream, identity, payload.get(observed_at_key), digest, body, now))
        if not values:
            return 0
        with self.transaction() as connection:
            before = connection.total_changes
            connection.executemany(
                """INSERT OR IGNORE INTO event_records
                   (stream,identity,observed_at,content_hash,payload_json,inserted_at)
                   VALUES (?,?,?,?,?,?)""", values,
            )
            return connection.total_changes - before

    def events(self, stream: str, *, through: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM event_records WHERE stream=?"
        params: list[Any] = [stream]
        if through is not None:
            sql += " AND observed_at<=?"
            params.append(through)
        sql += " ORDER BY observed_at,id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def export_document(self, name: str, path: Path, *, indent: int | None = 1) -> None:
        payload = self.get_document(name)
        if payload is None:
            raise KeyError(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".db-export.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=indent),
                             encoding="utf-8")
        temporary.replace(path)

    def export_events(self, stream: str, path: Path) -> None:
        rows = self.events(stream)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".db-export.tmp")
        temporary.write_text("".join(self._canonical(row) + "\n" for row in rows),
                             encoding="utf-8")
        temporary.replace(path)

    def export_events_csv(self, stream: str, path: Path,
                          fieldnames: Iterable[str]) -> None:
        """이벤트 원장을 기존 CSV 소비자가 읽을 수 있는 형태로 재생성한다."""
        fields = list(fieldnames)
        rows = self.events(stream)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".db-export.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)

    def replace_dataset_csv(self, name: str, source: Path) -> int:
        """Explicit legacy import only; normal producers call replace_dataset_rows."""
        with source.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            if not fields:
                raise ValueError(f"CSV header missing: {source}")
            return self.replace_dataset_rows(name, reader, fields)

    def export_dataset_csv(self, name: str, path: Path) -> None:
        with self.connect() as connection:
            meta = connection.execute(
                "SELECT fieldnames_json FROM dataset_revisions WHERE name=?", (name,)
            ).fetchone()
            if meta is None:
                raise KeyError(name)
            fields = json.loads(meta["fieldnames_json"])
            cursor = connection.execute(
                "SELECT payload_json FROM dataset_rows WHERE dataset=? ORDER BY ordinal",
                (name,),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".db-export.tmp")
            with temporary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in cursor:
                    writer.writerow(json.loads(row["payload_json"]))
            temporary.replace(path)

    def insert_odds(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = []
        for row in rows:
            try:
                odds = [float(value) for value in str(row["odds"]).split(",") if value]
                n_way = (int(row["n_way"])
                         if row.get("n_way") not in (None, "") else None)
                values.append((
                    str(row["ts"]), int(row["year"]), int(row["round"]),
                    str(row["game_no"]), row.get("sport"), row.get("league"),
                    row.get("market_family"), n_way, row.get("market_label"),
                    row.get("home"), row.get("away"), row.get("date_text"),
                    json.dumps(odds, separators=(",", ":")), row.get("result"),
                ))
            except (KeyError, TypeError, ValueError):
                # 일부 옛 CSV에는 쉼표 escaping 오류로 열이 밀린 행이 있다.
                # 한 손상 행 때문에 수백만 정상 관측의 이관을 중단하지 않는다.
                continue
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

    def export_odds_csv(self, path: Path, *, day: str | None = None) -> None:
        """배당 스냅샷 호환 CSV를 DB 원장에서 재생성한다."""
        sql = """SELECT observed_at,season,round,game_no,sport,league,
                        market_family,n_way,market_label,home,away,date_text,
                        odds_json,result FROM odds_snapshots"""
        params: list[Any] = []
        if day is not None:
            sql += " WHERE substr(observed_at,1,10)=?"
            params.append(day)
        sql += " ORDER BY observed_at,season,round,game_no"
        fields = ["ts", "year", "round", "game_no", "sport", "league",
                  "market_family", "n_way", "market_label", "home", "away",
                  "date_text", "odds", "result"]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".db-export.tmp")
        with self.connect() as connection, temporary.open(
                "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in connection.execute(sql, params):
                writer.writerow({
                    "ts": row["observed_at"], "year": row["season"],
                    "round": row["round"], "game_no": row["game_no"],
                    "sport": row["sport"], "league": row["league"],
                    "market_family": row["market_family"], "n_way": row["n_way"],
                    "market_label": row["market_label"], "home": row["home"],
                    "away": row["away"], "date_text": row["date_text"],
                    "odds": ",".join(str(value) for value in
                                     json.loads(row["odds_json"])),
                    "result": row["result"],
                })
        temporary.replace(path)

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

    def get_artifact(self, name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM artifacts WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def get_artifact_json(self, name: str) -> tuple[str, str] | None:
        """Return the stored wire payload and revision without decoding it."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json,stored_at FROM artifacts WHERE name=?", (name,)
            ).fetchone()
        if row is None:
            return None
        return str(row["payload_json"]), str(row["stored_at"])

    def artifact_metadata(self, name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT name,generated_at,stored_at,length(payload_json) payload_bytes
                   FROM artifacts WHERE name=?""", (name,)
            ).fetchone()
        return dict(row) if row is not None else None

    def export_artifact(self, name: str, path: Path, *, indent: int | None = 1) -> None:
        payload = self.get_artifact(name)
        if payload is None:
            raise KeyError(name)
        _atomic_json(path, payload, indent=indent)

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in ("odds_snapshots", "prediction_records", "artifacts")
            }

    def migration_is_current(self, source: str, fingerprint: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM migration_state WHERE source=?", (source,)
            ).fetchone()
        return row is not None and row["fingerprint"] == fingerprint

    def mark_migrated(self, source: str, fingerprint: str, row_count: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO migration_state(source,fingerprint,imported_at,row_count)
                   VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                   fingerprint=excluded.fingerprint,
                   imported_at=excluded.imported_at,row_count=excluded.row_count""",
                (source, fingerprint, now, int(row_count)),
            )


def _atomic_json(path: Path, payload: Any, *, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".json-export.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=indent),
                         encoding="utf-8")
    temporary.replace(path)


def load_artifact(name: str, path: Path) -> dict[str, Any] | None:
    """Load runtime state from its only valid source for this environment."""
    if database_enabled():
        return RuntimeDatabase().get_artifact(name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_document(name: str, path: Path) -> Any | None:
    """DB misses remain misses; old exported files must never re-enter production."""
    if database_enabled():
        return RuntimeDatabase().get_document(name)
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_frame(name: str, path: Path, **kwargs):
    """Read typed tabular inputs directly from DB; path is a dev fixture only."""
    from runtime_frames import read_frame as read
    return read(name, path, **kwargs)


def persist_frame(name: str, frame, path: Path) -> None:
    if database_enabled():
        from runtime_frames import frame_rows
        RuntimeDatabase().replace_dataset_rows(name, frame_rows(frame), list(frame.columns))
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)


def persist_document(name: str, payload: Any, path: Path,
                     *, indent: int | None = 1) -> None:
    """운영에서는 DB에만 저장하고, 개발 환경에서만 파일 fixture를 갱신한다."""
    if database_enabled():
        RuntimeDatabase().put_document(name, payload)
    else:
        _atomic_json(path, payload, indent=indent)


def persist_artifact(name: str, payload: Mapping[str, Any], path: Path,
                     *, indent: int | None = 1) -> None:
    """운영 산출물은 DB에만 저장한다. ``path``는 로컬 fixture 전용이다."""
    if database_enabled():
        RuntimeDatabase().store_artifact(name, payload)
    else:
        _atomic_json(path, payload, indent=indent)


# GitHub Pages 로 배포되는 정적 사이트가 직접 읽는 산출물. 운영에서 생성기는
# DB(artifacts 테이블)에만 쓰므로, git push 직전에 이 목록을 docs/data/*.json 으로
# 내보내지 않으면 사이트가 마이그레이션 시점 값에서 영구히 멈춘다.
SITE_ARTIFACTS = ("live_odds", "picks", "picks_v2", "today", "today_combo",
                  "live_scores", "loss_grades", "combo", "info_lag")
# 폴링마다 바뀌는 실시간 산출물은 압축(indent 없음)으로 내보낸다.
_COMPACT_SITE_ARTIFACTS = {"live_odds", "live_scores"}


def export_site_artifacts(root: Path | None = None) -> list[str]:
    """DB 정본 산출물을 사이트가 읽는 ``docs/data/*.json`` 으로 내보낸다.

    운영(``PROODD_DB_PATH`` 설정)에서만 동작한다. DB에 없는 이름은 조용히 건너뛴다.
    실제로 내보낸 산출물 이름 목록을 돌려준다.
    """
    if not database_enabled():
        return []
    base = (root or ROOT) / "docs" / "data"
    database = RuntimeDatabase()
    written: list[str] = []
    for name in SITE_ARTIFACTS:
        indent = None if name in _COMPACT_SITE_ARTIFACTS else 1
        try:
            database.export_artifact(name, base / f"{name}.json", indent=indent)
        except KeyError:
            continue
        written.append(name)
    return written
