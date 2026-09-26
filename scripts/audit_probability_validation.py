"""Read a local JSONL ledger; print readiness only. Never opens production DB."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from probability_validation_audit import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256()
    def records():
        with args.ledger.open('rb') as handle:
            for line in handle:
                digest.update(line)
                if line.strip():
                    yield json.loads(line)
    result = audit(records())
    result['input_sha256'] = digest.hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
