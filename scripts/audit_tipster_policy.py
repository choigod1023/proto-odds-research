"""Read and authenticate a saved ledger, then report voting and fallback separately."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prediction_ledger import PredictionLedger
from tipster_policy_performance import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.ledger.read_bytes()
    if args.ledger.suffix == ".gz":
        raw = gzip.decompress(raw)
    records = [json.loads(line) for line in raw.splitlines() if line]
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    verifier._read_verified_storage()
    result = audit(records, evaluated_at=args.evaluated_at)
    result.update(hash_chain_verified=True, ledger_sha256=hashlib.sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {a: b for a, b in v.items() if a not in {"records", "by_sport"}}
                      for k, v in result["cohorts"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
