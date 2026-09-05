"""DB-native datasets and indexed, confirmed physical match history.

Staging is a temporary SQLite database, never an operational CSV. Parsing runs
outside the live database write lock; a complete dataset set is published once.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import tempfile
from zoneinfo import ZoneInfo


RESULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_results (
 source TEXT NOT NULL, season INTEGER NOT NULL, round INTEGER NOT NULL,
 game_no TEXT NOT NULL, kickoff TEXT NOT NULL, league TEXT NOT NULL,
 sport TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
 home_score INTEGER NOT NULL, away_score INTEGER NOT NULL, observed_at TEXT NOT NULL,
 result_state TEXT NOT NULL,
 PRIMARY KEY(source,season,round,game_no,observed_at)
);
CREATE INDEX IF NOT EXISTS idx_results_kickoff ON match_results(kickoff,league);
"""
RESULT_INSERT = """INSERT INTO match_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(source,season,round,game_no,observed_at) DO UPDATE SET
 kickoff=excluded.kickoff,league=excluded.league,sport=excluded.sport,
 home_team=excluded.home_team,away_team=excluded.away_team,
 home_score=excluded.home_score,away_score=excluded.away_score,
 result_state=excluded.result_state"""
_RESULT_VALUES = "kickoff,league,sport,home_team,away_team,home_score,away_score,result_state"


def append_staged_results(connection):
    # Preserve the first availability time of unchanged results and every actual
    # correction, without copying the full history again each publish cycle.
    comparisons = " OR ".join(f"s.{key}!=p.{key}" for key in _RESULT_VALUES.split(","))
    available_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    connection.execute(f"""INSERT OR IGNORE INTO match_results
        SELECT s.source,s.season,s.round,s.game_no,s.kickoff,s.league,s.sport,
          s.home_team,s.away_team,s.home_score,s.away_score,?,s.result_state
        FROM staged.match_results s LEFT JOIN match_results p ON
        p.source=s.source AND p.season=s.season AND p.round=s.round AND p.game_no=s.game_no
        AND p.observed_at=(SELECT MAX(x.observed_at) FROM match_results x WHERE
          x.source=s.source AND x.season=s.season AND x.round=s.round AND x.game_no=s.game_no)
        WHERE p.source IS NULL OR {comparisons}""", (available_at,))


def result_row(row, source, observed_at):
    """Only unadjusted final score rows qualify; never handicap/OU/live scores."""
    if row.get("market_family") not in ("승패", "승무패"):
        return None
    try:
        year, rnd = int(row.get("year", row.get("season"))), int(row["round"])
        stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None or row.get("game_no") is None:
            return None
        observed_at = stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError, KeyError):
        return None
    # Keep invalidations too, so a newer cancelled/pending/corrupt result cannot
    # leave a previously confirmed score active forever.
    invalid = (source, year, rnd, str(row["game_no"]), "0001-01-01T00:00:00",
               str(row.get("league") or ""), str(row.get("sport") or ""), "", "",
               0, 0, observed_at, "unconfirmed")
    if str(row.get("is_void", "")).lower() in ("true", "1") or row.get("result") not in ("홈승", "홈패", "무승부"):
        return invalid
    home = re.fullmatch(r"(.+?)\s+(\d+)", str(row.get("home", "")).strip())
    away = re.fullmatch(r"(\d+)\s+(.+)", str(row.get("away", "")).strip())
    date = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", str(row.get("date_text", "")))
    if not home or not away or not date:
        return invalid
    try:
        month, day, hour, minute = map(int, date.groups())
        kickoff = datetime(year - int(rnd == 1 and month == 12), month, day, hour, minute)
        hs, aws = int(home[2]), int(away[1])
    except (TypeError, ValueError, KeyError):
        return invalid
    expected = "홈승" if hs > aws else "홈패" if hs < aws else "무승부"
    if row["result"] != expected or not row.get("league") or row.get("game_no") is None:
        return invalid
    return (source, year, rnd, str(row["game_no"]), kickoff.isoformat(),
            str(row["league"]), str(row.get("sport") or ""), home[1], away[2],
            hs, aws, observed_at, "confirmed")


class DatasetStore:
    def document_names(self, prefix=""):
        with self.connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT name FROM documents WHERE substr(name,1,?)=? ORDER BY name",
                (len(prefix), prefix))]

    def dataset_metadata(self, name):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM dataset_revisions WHERE name=?", (name,)).fetchone()
        return dict(row) if row is not None else None

    def iter_dataset(self, name):
        with self.connect() as connection:
            connection.execute("BEGIN")
            if connection.execute("SELECT 1 FROM dataset_revisions WHERE name=?", (name,)).fetchone() is None:
                raise KeyError(f"DB dataset missing: {name}; run explicit migration")
            for row in connection.execute(
                "SELECT payload_json FROM dataset_rows WHERE dataset=? ORDER BY ordinal", (name,)):
                yield json.loads(row[0])

    def replace_dataset_rows(self, name, rows, fieldnames, **conditions):
        return self.replace_datasets_rows({name: (rows, fieldnames)}, **conditions)[name]

    def replace_datasets_rows(self, datasets, **conditions):
        def records():
            for name, (rows, _fields) in datasets.items():
                for row in rows:
                    yield name, row
        return self.replace_datasets_records(
            {name: fields for name, (_rows, fields) in datasets.items()}, records(), **conditions)

    def replace_datasets_records(self, fieldnames, records, *, expected_revisions=None, insert_only=False):
        """One-pass multi-dataset staging; parsing holds no live DB transaction."""
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        fields = {name: list(value) for name, value in fieldnames.items()}
        for name, value in fields.items():
            if not value or len(value) != len(set(value)):
                raise ValueError(f"invalid fields: {name}")
        counts = {name: 0 for name in fields}
        digests = {name: hashlib.sha256(json.dumps(value).encode()) for name, value in fields.items()}
        with tempfile.TemporaryDirectory(prefix="proodd-stage-", dir=self.path.parent) as directory:
            stage = Path(directory) / "datasets.sqlite3"
            with closing(sqlite3.connect(stage)) as staged:
                staged.executescript("""
                  CREATE TABLE dataset_revisions(name TEXT PRIMARY KEY,revision INTEGER,
                    fieldnames_json TEXT,row_count INTEGER,content_hash TEXT,generated_at TEXT);
                  CREATE TABLE dataset_rows(dataset TEXT,ordinal INTEGER,payload_json TEXT,
                    PRIMARY KEY(dataset,ordinal));
                """ + RESULT_SCHEMA)
                for name, original in records:
                    row = {key: original.get(key) for key in fields[name]}
                    body = self._canonical(row)
                    counts[name] += 1
                    digests[name].update(body.encode("utf-8") + b"\n")
                    staged.execute("INSERT INTO dataset_rows VALUES (?,?,?)", (name, counts[name], body))
                    if name == "processed_games":
                        result = result_row(row, "dataset", now)
                        if result:
                            staged.execute(RESULT_INSERT, result)
                for name, value in fields.items():
                    staged.execute("INSERT INTO dataset_revisions VALUES (?,?,?,?,?,?)",
                                   (name, 1, json.dumps(value), counts[name], digests[name].hexdigest(), now))
                staged.commit()
            return self.publish_datasets_from(stage, list(fields), expected_revisions=expected_revisions, insert_only=insert_only)

    def publish_datasets_from(self, staged_path, names, *, expected_revisions=None, insert_only=False):
        revisions = {}
        with self.connect() as connection:
            connection.execute("ATTACH DATABASE ? AS staged", (str(staged_path),))
            try:
                connection.execute("BEGIN IMMEDIATE")
                for name in names:
                    new = connection.execute("SELECT * FROM staged.dataset_revisions WHERE name=?", (name,)).fetchone()
                    if new is None:
                        raise KeyError(name)
                    old = connection.execute("SELECT * FROM dataset_revisions WHERE name=?", (name,)).fetchone()
                    if expected_revisions is not None and name in expected_revisions:
                        if (old["revision"] if old else None) != expected_revisions[name]:
                            raise RuntimeError(f"Dataset changed during staging: {name}")
                    if insert_only and old is not None:
                        revisions[name] = int(old["revision"])
                        continue
                    if old is not None and old["content_hash"] == new["content_hash"]:
                        if name == "processed_games":
                            append_staged_results(connection)
                        revisions[name] = int(old["revision"])
                        continue
                    revision = 1 if old is None else int(old["revision"]) + 1
                    connection.execute("DELETE FROM dataset_rows WHERE dataset=?", (name,))
                    connection.execute("""INSERT INTO dataset_revisions VALUES (?,?,?,?,?,?)
                       ON CONFLICT(name) DO UPDATE SET revision=excluded.revision,
                       fieldnames_json=excluded.fieldnames_json,row_count=excluded.row_count,
                       content_hash=excluded.content_hash,generated_at=excluded.generated_at""",
                       (name, revision, new["fieldnames_json"], new["row_count"], new["content_hash"], new["generated_at"]))
                    connection.execute("INSERT INTO dataset_rows SELECT * FROM staged.dataset_rows WHERE dataset=?", (name,))
                    if name == "processed_games":
                        append_staged_results(connection)
                    revisions[name] = revision
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("DETACH DATABASE staged")
        return revisions

    def record_match_rows(self, rows, source="proto"):
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        values = [value for row in rows if (value := result_row(row, source, str(row.get("ts") or now)))]
        if not values:
            return 0
        with self.transaction() as connection:
            before = connection.total_changes
            for value in values:
                previous = connection.execute("""SELECT * FROM match_results
                    WHERE source=? AND season=? AND round=? AND game_no=?
                    ORDER BY observed_at DESC LIMIT 1""", value[:4]).fetchone()
                if previous is not None:
                    old = tuple(previous)
                    if old[:11] + old[12:] == value[:11] + value[12:] and old[11] <= value[11]:
                        continue
                connection.execute(RESULT_INSERT, value)
            return connection.total_changes - before

    def match_history(self, sports=None, before=None):
        # Prefer current source corrections over archived dataset copies. Across
        # reissued markets, disagreements are withheld instead of picking a score.
        params = []
        availability = ""
        if before is not None:
            cutoff = datetime.fromisoformat(str(before).replace("Z", "+00:00"))
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=ZoneInfo("Asia/Seoul"))
            before = cutoff.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None).isoformat()
            availability = "WHERE observed_at<=?"
            params.append(cutoff.astimezone(timezone.utc).isoformat(timespec="microseconds"))
        sql = f"""WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY season,round,game_no
              ORDER BY (source='proto') DESC,observed_at DESC) AS rn FROM match_results {availability}
          ), physical AS (
            SELECT kickoff,league,sport,home_team,away_team,MIN(home_score) home_score,
              MIN(away_score) away_score FROM ranked WHERE rn=1 AND result_state='confirmed'
            GROUP BY kickoff,league,sport,home_team,away_team
            HAVING MIN(home_score)=MAX(home_score) AND MIN(away_score)=MAX(away_score)
          ) SELECT * FROM physical WHERE 1=1"""
        if before is not None:
            sql += " AND kickoff<?"
            params.append(str(before))
        if sports:
            sql += " AND sport IN (" + ",".join("?" for _ in sports) + ")"
            params.extend(sports)
        sql += " ORDER BY kickoff,league,home_team"
        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params)]
        for row in rows:
            row.update(date=row["kickoff"][:10], year=int(row["kickoff"][:4]),
                       outcome=1.0 if row["home_score"] > row["away_score"] else
                       0.0 if row["home_score"] < row["away_score"] else 0.5)
        return rows
