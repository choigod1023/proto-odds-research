"""Idempotently import legacy odds CSVs, prediction JSONL, and web artifacts."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from runtime_db import ROOT, RuntimeDatabase


DOCUMENT_SOURCES = {
    "player_info": "data/raw/player_info.json",
    "baseball_context_state": "data/raw/baseball_context/_state.json",
    "pickster_state": "data/raw/picksters/_state.json",
    "info_watch_state": "data/raw/info_watch/_state.json",
    "llm_budget": "data/raw/llm_cache/budget.json",
    "llm_commentary_cache": "data/raw/llm_cache/commentary.json",
    "kbo_starters": "data/raw/kbo_starters.json",
    "mlb_starters": "data/raw/mlb_starters.json",
    "npb_starters": "data/raw/npb_starters.json",
    "overseas_kbo": "data/raw/overseas/KBO.json",
    "overseas_kleague1": "data/raw/overseas/K리그1.json",
    "overseas_mlb": "data/raw/overseas/MLB.json",
    "overseas_npb": "data/raw/overseas/NPB.json",
    "detail_kbo_baseball": "data/raw/detail/kbo_baseball_2023_2026.json",
    "detail_kbo_batters": "data/raw/detail/kbo_batters_2023_2026.json",
    "detail_kbo_batters_indiv": "data/raw/detail/kbo_batters_indiv_2023_2026.json",
    "detail_kleague_shots": "data/raw/detail/kleague_shots_2023_2026.json",
    "detail_kleague_soccer": "data/raw/detail/kleague_soccer_2023_2026.json",
    "processed_live_baseball_features": "data/processed/live_baseball_features.json",
    "processed_pickster_eval": "data/processed/pickster_eval.json",
    "processed_team_map": "data/processed/team_map.json",
}

EVENT_SOURCES = {
    "baseball_context_events": "data/raw/baseball_context/events.jsonl",
    "pickster_crowd": "data/raw/picksters/tailslips_crowd.jsonl",
    "pickster_leaderboard": "data/raw/picksters/tailslips_leaderboard.jsonl",
    "pickster_pick_events": "data/raw/picksters/tailslips_pick_events.jsonl",
    "weather_forecasts": "data/raw/weather/forecast_snapshots.jsonl",
    "xg_snapshots": "data/raw/xg_snapshots.jsonl",
    "fotmob_xg": "data/raw/fotmob_xg.jsonl",
    "recommendation_revisions": "data/raw/recommendation_revisions.jsonl",
    "starter_changes": "data/raw/info_watch/starter_changes.jsonl",
}

CSV_EVENT_SOURCES = {
    "starter_announcements": "data/raw/info_watch/starter_announcements.csv",
    "overseas_live_odds": "data/raw/overseas/live_odds.csv",
    "odds_changes": "data/raw/snapshots/changes.csv",
}

DATASET_SOURCES = {
    "processed_games": "data/processed/games.csv",
    "processed_bets": "data/processed/bets.csv",
}


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _migrate_runtime_sources(root: Path, db: RuntimeDatabase, *,
                             include_datasets: bool = True) -> tuple[int, int, int]:
    documents = events = datasets = 0
    for name, relative in DOCUMENT_SOURCES.items():
        path = root / relative
        if not path.exists():
            continue
        fingerprint = _fingerprint(path)
        source = f"document:{relative}"
        if db.migration_is_current(source, fingerprint):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        db.put_document(name, payload)
        db.mark_migrated(source, fingerprint, 1)
        documents += 1

    for stream, relative in EVENT_SOURCES.items():
        path = root / relative
        if not path.exists():
            continue
        fingerprint = _fingerprint(path)
        source = f"events:{relative}"
        if db.migration_is_current(source, fingerprint):
            continue
        rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip())
        batch, seen = [], 0
        for row in rows:
            batch.append(row)
            seen += 1
            if len(batch) >= 2000:
                events += db.append_events(stream, batch)
                batch.clear()
        events += db.append_events(stream, batch)
        db.mark_migrated(source, fingerprint, seen)

    for stream, relative in CSV_EVENT_SOURCES.items():
        path = root / relative
        if not path.exists():
            continue
        fingerprint = _fingerprint(path)
        source = f"events:{relative}"
        if db.migration_is_current(source, fingerprint):
            continue
        seen = 0
        with path.open(newline="", encoding="utf-8") as handle:
            batch = []
            for row in csv.DictReader(handle):
                batch.append(row)
                seen += 1
                if len(batch) >= 2000:
                    events += db.append_events(stream, batch)
                    batch.clear()
            events += db.append_events(stream, batch)
        db.mark_migrated(source, fingerprint, seen)

    for name, relative in DATASET_SOURCES.items() if include_datasets else ():
        path = root / relative
        if not path.exists():
            continue
        fingerprint = _fingerprint(path)
        source = f"dataset:{relative}"
        if db.migration_is_current(source, fingerprint):
            continue
        db.replace_dataset_csv(name, path)
        with path.open(encoding="utf-8") as handle:
            row_count = max(sum(1 for _ in handle) - 1, 0)
        db.mark_migrated(source, fingerprint, row_count)
        datasets += 1
    return documents, events, datasets


def migrate(root: Path = ROOT, database: RuntimeDatabase | None = None,
            *, include_odds: bool = True, include_runtime_sources: bool = True,
            include_datasets: bool = True) -> dict[str, int]:
    db = database or RuntimeDatabase()
    odds = 0
    paths = sorted((root / "data/raw/snapshots").glob("odds_timeseries_*.csv")) \
        if include_odds else []
    for path in paths:
        stat = path.stat()
        source = str(path.relative_to(root))
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
        if db.migration_is_current(source, fingerprint):
            continue
        seen = 0
        with path.open(newline="", encoding="utf-8") as handle:
            batch = []
            for row in csv.DictReader(handle):
                seen += 1
                batch.append(row)
                if len(batch) >= 5000:
                    odds += db.insert_odds(batch)
                    batch.clear()
            odds += db.insert_odds(batch)
        db.mark_migrated(source, fingerprint, seen)

    predictions = 0
    ledger = root / "data/raw/prediction_ledger/pregame.jsonl"
    if ledger.exists():
        records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        predictions = db.mirror_prediction_records(records)

    artifacts = 0
    for name in ("live_odds", "picks_v2", "today_combo", "live_scores"):
        path = root / "docs/data" / f"{name}.json"
        if not path.exists():
            continue
        db.store_artifact(name, json.loads(path.read_text(encoding="utf-8")))
        artifacts += 1
    documents = events = datasets = 0
    if include_runtime_sources:
        documents, events, datasets = _migrate_runtime_sources(
            root, db, include_datasets=include_datasets)
    return {"odds_imported": odds, "predictions_imported": predictions,
            "artifacts_imported": artifacts, "documents_imported": documents,
            "events_imported": events, "datasets_imported": datasets, **db.counts()}


if __name__ == "__main__":
    critical = "--critical" in sys.argv
    print(json.dumps(migrate(include_odds=not critical,
                             include_runtime_sources=True,
                             include_datasets=not critical), ensure_ascii=False))
