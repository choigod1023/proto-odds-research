"""Explicit one-shot settlement operation; no collection, scheduling or deployment."""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from prediction_ledger import PredictionLedger
from reviewed_settlement_plan import read_plan, apply_reviewed_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/raw/prediction_ledger/pregame.jsonl")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    if args.plan.suffix == ".gz":
        raw = gzip.decompress(raw)
    plan = read_plan(raw, args.approved_sha256)
    if args.apply and os.environ.get("PROODD_DB_PATH"):
        available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        if available < 512 * 1024:
            raise RuntimeError("settlement deferred: less than 512 MiB headroom")
    ledger = PredictionLedger(args.ledger, lock_timeout=30)
    print(json.dumps(apply_reviewed_plan(ledger, plan, apply=args.apply)))


if __name__ == "__main__":
    main()
