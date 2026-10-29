"""Offline comparison of authenticated operating ledger and saved official rows."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from operating_model_comparison import compare
from prediction_ledger import PredictionLedger


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--official-results',type=Path,required=True)
    p.add_argument('--evaluated-at',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--stake',type=int,default=10000)
    args = p.parse_args()
    def read(path):
        return gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
    raw, results = read(args.ledger), read(args.official_results)
    records = [json.loads(line) for line in raw.splitlines() if line]
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    verifier._read_verified_storage()  # abort on any broken hash, including settlement rows
    report = compare(records,json.loads(results),evaluated_at=args.evaluated_at,stake=args.stake)
    report.update(hash_chain_verified=True, ledger_raw_sha256=hashlib.sha256(raw).hexdigest(),
                  official_raw_sha256=hashlib.sha256(results).hexdigest())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(hash_chain_verified=True,latest_events=report['latest_events'],
                         eligible_events=len(report['events']),policies=list(report['policies'])),ensure_ascii=False))


if __name__ == '__main__':
    main()
