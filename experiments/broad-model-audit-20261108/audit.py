"""Validate parallel study artifacts, without importing their metric implementations."""
import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def audit(inputs):
    results = {}
    for entry in inputs:
        path = Path(entry['path'])
        groups = defaultdict(list)
        identities = {}
        unique = set()
        for line in gzip.open(path, 'rt', encoding='utf8'):
            r = json.loads(line)
            p, y, odds = r['p'], r['y'], r['odds']
            assert len(p) == len(odds) and 0 <= y < len(p)
            assert all(math.isfinite(x) and 0 <= x <= 1 for x in p)
            assert abs(sum(p) - 1) < 1e-8
            choice = max(range(len(p)), key=p.__getitem__)
            assert r['pick'] == choice
            assert bool(r['hit']) == (choice == y)
            net = Decimal(str(odds[choice])) - 1 if choice == y else Decimal(-1)
            assert net == Decimal(str(r['net'])), (entry['family'], r['event_id'])
            product = r.get('family', r.get('product', 'winner'))
            key = (r['event_id'], product, r.get('line'))
            facts = (r['day'], tuple(odds), y)
            assert key not in identities or identities[key] == facts
            identities[key] = facts
            rowkey = (key, r['model'])
            assert rowkey not in unique
            unique.add(rowkey)
            group = (r['day'][:4], r['sport'], product, r['model'])
            groups[group].append(r)
        metrics = []
        for (year, sport, product, model), rows in sorted(groups.items()):
            n = len(rows)
            counts = sum(r['hit'] for r in rows)
            net = sum((Decimal(str(r['net'])) for r in rows), Decimal(0))
            loss = -sum(math.log(max(r['p'][r['y']], 1e-12)) for r in rows) / n
            metrics.append({'year': year, 'sport': sport, 'product': product, 'model': model,
                            'n': n, 'hits': counts, 'accuracy': counts/n, 'roi': float(net/n),
                            'logloss': loss,
                            'primary': not (entry['family'] in {'graph', 'regime'} and sport == 'sc')})
        results[entry['family']] = {'pr': entry['pr'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                    'event_products': len(identities), 'model_rows': len(unique),
                                    'audit': 'passed', 'metrics': metrics}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.manifest.read_text(encoding='utf8')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps({k: {f:v[f] for f in ['audit', 'event_products', 'model_rows']} for k,v in result.items()}))


if __name__ == '__main__':
    main()
