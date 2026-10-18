"""Read a local JSONL ledger; print readiness only. Never opens production DB."""
import argparse
import hashlib
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from probability_validation_audit import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--operating', action='store_true',
                        help='Audit operating picks without requiring an AI candidate')
    args = parser.parse_args()
    digest = hashlib.sha256()
    def records():
        opener = gzip.open if args.ledger.suffix == '.gz' else open
        with opener(args.ledger, 'rb') as handle:
            for line in handle:
                digest.update(line)
                if line.strip():
                    yield json.loads(line)
    result = audit(records(), require_candidate=not args.operating)
    result['input_sha256'] = digest.hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
