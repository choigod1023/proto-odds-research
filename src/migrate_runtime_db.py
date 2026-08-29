"""Idempotently import legacy odds CSVs, prediction JSONL, and web artifacts."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from runtime_db import ROOT, RuntimeDatabase


def migrate(root: Path = ROOT, database: RuntimeDatabase | None = None,
            *, include_odds: bool = True) -> dict[str, int]:
    db = database or RuntimeDatabase()
    odds = 0
    paths = sorted((root / "data/raw/snapshots").glob("odds_timeseries_*.csv")) \
        if include_odds else []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            batch = []
            for row in csv.DictReader(handle):
                batch.append(row)
                if len(batch) >= 5000:
                    odds += db.insert_odds(batch)
                    batch.clear()
            odds += db.insert_odds(batch)

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
    return {"odds_imported": odds, "predictions_imported": predictions,
            "artifacts_imported": artifacts, **db.counts()}


if __name__ == "__main__":
    print(json.dumps(migrate(include_odds="--critical" not in sys.argv), ensure_ascii=False))
