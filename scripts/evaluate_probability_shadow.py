"""Read-only local JSONL evaluation of captured shadow/market pairs."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shadow_probability_evaluation import evaluate

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args()
    with args.ledger.open(encoding="utf-8") as handle:
        result = evaluate(json.loads(line) for line in handle if line.strip())
    print(json.dumps(result, ensure_ascii=False, indent=2))
