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


RESULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_results (
 source TEXT NOT NULL, season INTEGER NOT NULL, round INTEGER NOT NULL,
 game_no TEXT NOT NULL, kickoff TEXT NOT NULL, league TEXT NOT NULL,
 sport TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
 home_score INTEGER NOT NULL, away_score INTEGER NOT NULL, observed_at TEXT NOT NULL,
 PRIMARY KEY(source,season,round,game_no)
);
CREATE INDEX IF NOT EXISTS idx_results_kickoff ON match_results(kickoff,league);
"""
RESULT_INSERT = """INSERT INTO match_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(source,season,round,game_no) DO UPDATE SET
 kickoff=excluded.kickoff,league=excluded.league,sport=excluded.sport,
 home_team=excluded.home_team,away_team=excluded.away_team,
 home_score=excluded.home_score,away_score=excluded.away_score,
 observed_at=excluded.observed_at WHERE excluded.observed_at>=match_results.observed_at"""


def result_row(row, source, observed_at):
    """Only unadjusted final score rows qualify; never handicap/OU/live scores."""
    if str(row.get("is_void", "")).lower() in ("true", "1"):
        return None
    if row.get("market_family") not in ("승패", "승무패"):
        return None
    if row.get("result") not in ("홈승", "홈패", "무승부"):
        return None
    home = re.fullmatch(r"(.+?)\s+(\d+)", str(row.get("home", "")).strip())
    away = re.fullmatch(r"(\d+)\s+(.+)", str(row.get("away", "")).strip())
    date = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", str(row.get("date_text", "")))
    if not home or not away or not date:
        return None
    try:
        year, rnd = int(row.get("year", row.get("season"))), int(row["round"])
        month, day, hour, minute = map(int, date.groups())
        kickoff = datetime(year - int(rnd == 1 and month == 12), month, day, hour, minute)
        hs, aws = int(home[2]), int(away[1])
    except (TypeError, ValueError, KeyError):
        return None
    expected = "홈승" if hs > aws else "홈패" if hs < aws else "무승부"
    if row["result"] != expected or not row.get("league") or row.get("game_no") is None:
        return None
    return (source, year, rnd, str(row["game_no"]), kickoff.isoformat(),
            str(row["league"]), str(row.get("sport") or ""), home[1], away[2],
            hs, aws, observed_at)


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

    def replace_dataset_rows(self, name, rows, fieldnames):
        return self.replace_datasets_rows({name: (rows, fieldnames)})[name]

    def replace_datasets_rows(self, datasets):
        """Bounded-memory staging followed by all-or-nothing live publication."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory(prefix="proodd-stage-", dir=self.path.parent) as directory:
            stage = Path(directory) / "datasets.sqlite3"
            with closing(sqlite3.connect(stage)) as staged:
                staged.executescript("""
                  CREATE TABLE dataset_revisions(name TEXT PRIMARY KEY,revision INTEGER,
                    fieldnames_json TEXT,row_count INTEGER,content_hash TEXT,generated_at TEXT);
                  CREATE TABLE dataset_rows(dataset TEXT,ordinal INTEGER,payload_json TEXT,
                    PRIMARY KEY(dataset,ordinal));
                """ + RESULT_SCHEMA)
                for name, (rows, fieldnames) in datasets.items():
                    fields = list(fieldnames)
                    if not fields or len(fields) != len(set(fields)):
                        raise ValueError(f"invalid fields: {name}")
                    digest = hashlib.sha256(json.dumps(fields).encode())
                    count = 0
                    for count, original in enumerate(rows, 1):
                        row = {key: original.get(key) for key in fields}
                        body = self._canonical(row)
                        digest.update(body.encode("utf-8") + b"\n")
                        staged.execute("INSERT INTO dataset_rows VALUES (?,?,?)", (name, count, body))
                        if name == "processed_games":
                            result = result_row(row, "dataset", now)
                            if result:
                                staged.execute(RESULT_INSERT, result)
                    staged.execute("INSERT INTO dataset_revisions VALUES (?,?,?,?,?,?)",
                                   (name, 1, json.dumps(fields), count, digest.hexdigest(), now))
                staged.commit()
            return self.publish_datasets_from(stage, list(datasets))

    def publish_datasets_from(self, staged_path, names):
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
                    if old is not None and old["content_hash"] == new["content_hash"]:
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
                        connection.execute("DELETE FROM match_results WHERE source='dataset'")
                        connection.execute("INSERT INTO match_results SELECT * FROM staged.match_results WHERE source='dataset'")
                    revisions[name] = revision
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("DETACH DATABASE staged")
        return revisions

    def record_match_rows(self, rows, source="proto"):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = [value for row in rows if (value := result_row(row, source, str(row.get("ts") or now)))]
        if not values:
            return 0
        with self.transaction() as connection:
            before = connection.total_changes
            connection.executemany(RESULT_INSERT, values)
            return connection.total_changes - before

    def match_history(self, sports=None, before=None):
        # Prefer current source corrections over archived dataset copies. Across
        # reissued markets, disagreements are withheld instead of picking a score.
        sql = """WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY season,round,game_no
              ORDER BY (source='proto') DESC,observed_at DESC) AS rn FROM match_results
          ), physical AS (
            SELECT kickoff,league,sport,home_team,away_team,MIN(home_score) home_score,
              MIN(away_score) away_score FROM ranked WHERE rn=1
            GROUP BY kickoff,league,sport,home_team,away_team
            HAVING MIN(home_score)=MAX(home_score) AND MIN(away_score)=MAX(away_score)
          ) SELECT * FROM physical WHERE 1=1"""
        params = []
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
