"""Independent settlement and scalar metrics; imports NO study implementation."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np  # RNG only: exactly the preregistered resampling indices.

HERE = Path(__file__).resolve().parent


def read_lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        return [json.loads(s) for s in file]


def settle(row, name):
    p = row['probabilities'][name]
    direction = 0 if p >= .5 else 1
    hit = int(row['outcome'] == direction)
    payout = Decimal(str(row['odds'][direction])) * 100 if hit else Decimal(0)
    assert payout == payout.to_integral_value(), 'fractional cents'
    return dict(pick=direction, correct=hit, profit_cents=int(payout) - 100)


def summary(rows, name):
    events = [settle(r, name) for r in rows]
    n = len(events)
    wins = sum(e['correct'] for e in events)
    profit = sum(e['profit_cents'] for e in events)
    home = sum(e['pick'] == 0 for e in events)
    loss, squared = [], []
    for row in rows:
        p = max(1e-15, min(1 - 1e-15, row['probabilities'][name]))
        target = int(row['outcome'] == 0)
        loss.append(-math.log(p if target else 1 - p))
        squared.append(2 * (p - target) ** 2)
    return dict(n=n, bets=n, coverage=1., wins=wins, accuracy=wins / n,
                profit_cents=profit, roi=profit / (100 * n),
                logloss=round(math.fsum(loss) / n, 12), brier=round(math.fsum(squared) / n, 12),
                home_picks=home, away_picks=n - home, home_share=home / n, home_constant_agreement=home == n)


def quantile(xs, q):
    xs = sorted(xs)
    i = q * (len(xs) - 1)
    a = int(i)
    b = min(a + 1, len(xs) - 1)
    return round(xs[a] + (xs[b] - xs[a]) * (i - a), 12)


def contrast(rows, model, baseline):
    groups = {}
    better = worse = 0
    for r in rows:
        p, q = settle(r, model), settle(r, baseline)
        key = date.fromisoformat(r['day']).isocalendar()[:2]
        group = groups.setdefault(key, [0, 0, 0])
        group[0] += 1
        group[1] += p['correct'] - q['correct']
        group[2] += p['profit_cents'] - q['profit_cents']
        better += int(p['correct'] == 1 and q['correct'] == 0)
        worse += int(p['correct'] == 0 and q['correct'] == 1)
    blocks = [groups[k] for k in sorted(groups)]
    draws = np.random.default_rng(20261030).integers(0, len(blocks), size=(5000, len(blocks)))
    accuracy, roi = [], []
    for ids in draws:
        n = sum(blocks[i][0] for i in ids)
        accuracy.append(sum(blocks[i][1] for i in ids) / n)
        roi.append((sum(blocks[i][2] for i in ids) / n) / 100)
    trials = better + worse
    raw = sum(math.comb(trials, k) for k in range(better, trials + 1)) / 2 ** trials if trials else 1.
    return dict(weeks=len(blocks), accuracy_delta=sum(g[1] for g in blocks) / len(rows),
                roi_delta=sum(g[2] for g in blocks) / (100 * len(rows)),
                accuracy_ci95=[quantile(accuracy, q) for q in [.025, .975]],
                roi_ci95=[quantile(roi, q) for q in [.025, .975]],
                accuracy_family_ci=[quantile(accuracy, q) for q in [.05 / 60 / 2, 1 - .05 / 60 / 2]],
                roi_family_ci=[quantile(roi, q) for q in [.05 / 60 / 2, 1 - .05 / 60 / 2]],
                discordant_improved=better, discordant_worsened=worse, p_raw=raw)


def check(folder):
    rows = read_lines(folder / 'predictions.jsonl.gz')
    result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    event_checks = metrics_checks = contrast_checks = 0
    for row in rows:
        for name, stored in row['settlements'].items():
            assert settle(row, name) == stored, (row['event_id'], name)
            event_checks += 1
    for item in result['summaries']:
        local = [r for r in rows if r['league'] == item['league']]
        assert summary(local, item['model']) == item['metrics'], (item['league'], item['model'])
        metrics_checks += 1
    for name, stored in result['overall'].items():
        assert summary(rows, name) == stored, name
        metrics_checks += 1
    for item in result['contrasts']:
        local = [r for r in rows if r['league'] == item['league']]
        actual = contrast(local, item['model'], item['baseline'])
        for key, value in actual.items():
            if key == 'p_raw':
                assert abs(value - item[key]) < 1e-14
            else:
                assert value == item[key], (item['league'], item['model'], key, value, item[key])
        contrast_checks += 1
    family = [r for r in result['contrasts'] if r['baseline'] in ['market', 'fixed9:1']]
    ordered = sorted(family, key=lambda r: r['p_raw'])
    for i, r in enumerate(ordered):
        adjusted = max(min(1., ordered[j]['p_raw'] * (60 - j)) for j in range(i + 1))
        assert adjusted == r['p_holm']
    selected = min(result['validation'], key=lambda k: (-result['validation'][k]['accuracy'],
                                                        result['validation'][k]['logloss'], k))
    assert selected == result['selected']
    return dict(independent=True, event_settlements_exact=event_checks, metric_groups_exact=metrics_checks,
                paired_weekly_contrasts_exact=contrast_checks, holm_family_exact=len(family),
                metric_precision='accuracy/ROI integer fractions exact; logloss/Brier and CI endpoints rounded12 decimals exact; scipy binomial tail agreement <1e-14',
                test_cases=len(rows), league_counts=dict(Counter(r['league'] for r in rows)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=HERE / 'results')
    args = parser.parse_args()
    status = check(args.results)
    (args.results / 'independent-verification.json').write_text(json.dumps(status, ensure_ascii=False, sort_keys=True) + '\n',
                                                              encoding='utf-8', newline='\n')
    print(json.dumps(status, ensure_ascii=False))


if __name__ == '__main__':
    main()
