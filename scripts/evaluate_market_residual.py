"""Read a verified local ledger; no DB access, fitting jobs or production writes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prediction_ledger import PredictionLedger
from market_residual_experiment import experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--sport", required=True)
    parser.add_argument("--version", required=True)
    for flag in ("train-end", "calibration-end", "test-end"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    if os.environ.get("PROODD_DB_PATH"):
        parser.error("Offline local copies only; unset PROODD_DB_PATH")
    if not args.ledger.is_file():
        parser.error("existing local ledger required")
    records = PredictionLedger(args.ledger).records()
    print(json.dumps(experiment(records, sport=args.sport, version=args.version,
        train_end=args.train_end, calibration_end=args.calibration_end,
        test_end=args.test_end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
