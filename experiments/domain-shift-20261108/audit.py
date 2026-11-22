"""Independent standard-library audit of emitted probabilities and unit settlement."""
import gzip
import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent


def holm(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    adjusted = [1.] * len(values)
    previous = 0.
    for rank, index in enumerate(order):
        previous = max(previous, min(1., values[index] * (len(values) - rank)))
        adjusted[index] = previous
    return adjusted


def main():
    summary = json.loads((HERE / 'summary.json').read_text(encoding='utf8'))
    groups = defaultdict(list)
    identities = {}
    for line in gzip.open(HERE / 'predictions.jsonl.gz', 'rt', encoding='utf8'):
        r = json.loads(line)
        p, y = r['p'], r['y']
        assert '2026-01-01' <= r['day'] <= '2026-08-26'
        assert all(math.isfinite(v) and 0 <= v <= 1 for v in p)
        assert abs(sum(p) - 1) < 1e-10
        assert len(p) == len(r['odds']) == (3 if r['sport'] == 'sc' else 2)
        choice = max(range(len(p)), key=lambda i: p[i])
        assert choice == r['pick'] and r['hit'] == (choice == y)
        net = Decimal(str(r['odds'][choice])) - 1 if choice == y else Decimal(-1)
        assert net == Decimal(r['net'])
        key = (r['sport'], r['event_id'])
        identity = (r['day'], r['league'], tuple(r['odds']), y)
        assert key not in identities or identities[key] == identity
        identities[key] = identity
        groups[(r['sport'], r['model'])].append(r)
    counts = {}
    for (sport, model), rows in groups.items():
        ids = [r['event_id'] for r in rows]
        assert len(set(ids)) == len(ids)
        baseline_ids = {r['event_id'] for r in groups[(sport, 'market')]}
        assert set(ids) == baseline_ids
        m = summary['sports'][sport]['models'][model]['all']
        accuracy = sum(r['hit'] for r in rows) / len(rows)
        roi = float(sum((Decimal(r['net']) for r in rows), Decimal(0)) / len(rows))
        ll = -sum(math.log(max(r['p'][r['y']], 1e-12)) for r in rows) / len(rows)
        assert m['n'] == len(rows)
        assert abs(accuracy - m['accuracy']) < 1e-12
        assert abs(roi - m['roi']) < 1e-12
        assert abs(ll - m['logloss']) < 1e-12
        counts[sport] = len(rows)
    hypotheses = []
    for sport, block in summary['sports'].items():
        for model in ['density_ratio_offset', 'margin_histogram_offset']:
            pair = block['models'][model]['paired']['market']
            for index, metric in enumerate(['accuracy', 'roi', 'logloss']):
                hypotheses.append({'sport': sport, 'model': model, 'metric': metric,
                                   'p': pair['paired_week_signflip_two_sided_p'][index]})
    for h, p in zip(hypotheses, holm([h['p'] for h in hypotheses])):
        h['holm_p_within_domain_family_24'] = p
    result = {'audit': 'passed', 'unique_events': len(identities), 'counts': counts,
              'prediction_rows': sum(map(len, groups.values())),
              'predictions_sha256': hashlib.sha256((HERE / 'predictions.jsonl.gz').read_bytes()).hexdigest(),
              'holm_scope': '2 candidate models x 4 sports x 3 metrics; not across all concurrent studies',
              'holm_rejections_005': sum(h['holm_p_within_domain_family_24'] < .05 for h in hypotheses),
              'hypotheses': hypotheses}
    (HERE / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k != 'hypotheses'}))


if __name__ == '__main__':
    main()
