"""Explicit SQLite -> CSV exports. Never invoked by collectors or scheduler.

Examples:
  python src/export_runtime.py --list
  python src/export_runtime.py --kind matches --output exports/matches.csv
  python src/export_runtime.py --kind dataset --name processed_games --output exports/games.csv
  python src/export_runtime.py --kind predictions --output exports/predictions.csv
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from runtime_db import configured_path


def _cell(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # Spreadsheet formula protection, without changing numeric values.
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                   or value.startswith(("\t", "\r", "\n"))):
        if not re.fullmatch(r"[+-]?\d+(\.\d+)?", value):
            return "'" + value
    return value


def export_csv(database_path: Path, output: Path, *, kind: str,
               name: str | None = None, overwrite=False):
    database_path, output = Path(database_path).resolve(), Path(output).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if output == database_path or str(output) in (str(database_path) + "-wal", str(database_path) + "-shm"):
        raise ValueError("output must not overwrite the database")
    if output.exists() and not overwrite:
        raise FileExistsError(f"{output}; use --overwrite explicitly")
    with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")  # consistent snapshot across field discovery + rows
        if kind == "dataset":
            meta = connection.execute("SELECT fieldnames_json FROM dataset_revisions WHERE name=?", (name,)).fetchone()
            if meta is None:
                raise KeyError(name)
            fields = json.loads(meta[0])
            sql, params, payload = "SELECT payload_json FROM dataset_rows WHERE dataset=? ORDER BY ordinal", (name,), True
        elif kind == "events":
            sql, params, payload = "SELECT payload_json FROM event_records WHERE stream=? ORDER BY id", (name,), True
            fields = None
        elif kind == "predictions":
            sql, params, payload = "SELECT record_json FROM prediction_records ORDER BY ledger_sequence", (), True
            fields = None
        elif kind in ("document", "artifact"):
            table = "documents" if kind == "document" else "artifacts"
            sql, params, payload = f"SELECT name,generated_at,payload_json FROM {table} WHERE name=?", (name,), False
            fields = ["name", "generated_at", "payload_json"]
            if connection.execute(f"SELECT 1 FROM {table} WHERE name=?", (name,)).fetchone() is None:
                raise KeyError(name)
        elif kind == "matches":
            # Export source observations too: provenance and reissue conflicts
            # stay inspectable. Predictions use deduplicated match_history().
            sql, params, payload = "SELECT * FROM match_results ORDER BY kickoff,source,season,round,game_no", (), False
            fields = [row[1] for row in connection.execute("PRAGMA table_info(match_results)")]
        elif kind == "odds":
            sql, params, payload = "SELECT * FROM odds_snapshots ORDER BY observed_at,id", (), False
            fields = [row[1] for row in connection.execute("PRAGMA table_info(odds_snapshots)")]
        else:
            raise ValueError(f"unknown export kind: {kind}")
        if fields is None:
            fields = list(dict.fromkeys(key for row in connection.execute(sql, params)
                                        for key in json.loads(row[0])))
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig",
                                         dir=output.parent, prefix=".db-export-", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writerow({field: _cell(field) for field in fields})
                for record in connection.execute(sql, params):
                    row = json.loads(record[0]) if payload else dict(record)
                    writer.writerow({key: _cell(value) for key, value in row.items()})
                    count += 1
            except Exception:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            if overwrite:
                os.replace(temporary, output)
            else:
                # Atomic creation that cannot race an existing user file.
                os.link(temporary, output)
                temporary.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=configured_path())
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--kind", choices=("dataset", "events", "predictions", "matches", "odds", "document", "artifact"))
    parser.add_argument("--name")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.list:
        with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            for kind, table, column in (("dataset", "dataset_revisions", "name"),
                    ("events", "event_records", "stream"), ("document", "documents", "name"),
                    ("artifact", "artifacts", "name")):
                for row in connection.execute(f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}"):
                    print(f"{kind}\t{row[0]}")
            print("matches\nodds\npredictions")
        return 0
    if not args.kind or not args.output:
        parser.error("--kind and --output are required")
    if args.kind in ("dataset", "events", "document", "artifact") and not args.name:
        parser.error("--name is required for this kind")
    count = export_csv(args.db, args.output, kind=args.kind, name=args.name, overwrite=args.overwrite)
    print(f"Exported {count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
