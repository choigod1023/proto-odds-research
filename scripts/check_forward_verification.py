"""Print a read-only status; no source requests or ledger mutations."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.kbo_forward.verification_status import inspect


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game-id', required=True)
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--since', required=True, help='prospective boundary with timezone offset')
    for arg in ('schedule-check', 'schedule-state', 'm1-ledger', 'legacy-ledger', 'common-ledger', 'archive-root', 'price-archive-root', 'parent-report'):
        p.add_argument('--'+arg, type=Path)
    args = vars(p.parse_args())
    print(json.dumps(inspect(**args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
