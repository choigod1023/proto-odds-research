"""Replay archived official odds into a NEW local ledger copy, never production."""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from live_market_refresh import settle_live_market_results
from prediction_runtime import PredictionRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("output", type=Path, help="New, nonexisting local ledger file")
    parser.add_argument("odds", type=Path, nargs="+", help="Archived JSON or JSON.gz feeds")
    args = parser.parse_args()
    if os.environ.get("PROODD_DB_PATH"):
        parser.error("Refusing DB mode: run offline without PROODD_DB_PATH")
    feeds = []
    for path in args.odds:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            feeds.append(json.load(handle))
    from live_market_refresh import _aware_timestamp
    if any(_aware_timestamp(doc.get("generated_at")) is None for doc in feeds):
        parser.error("All archived feeds must have timezone-aware timestamps")
    feeds.sort(key=lambda doc: _aware_timestamp(doc["generated_at"]))
    # Exclusive creation prevents overwriting the source or any other task's data.
    with args.ledger.open("rb") as source, args.output.open("xb") as target:
        shutil.copyfileobj(source, target)
    runtime = PredictionRuntime(args.output)
    before = len(runtime.records())  # Verify original chain before appending.
    appended = sum(settle_live_market_results(doc, runtime) for doc in feeds)
    print(json.dumps({"original_records": before, "appended_settlements": appended,
                      "output": str(args.output.resolve()),
                      "retrospective_recovery": True, "production_changed": False}))


if __name__ == "__main__":
    main()
