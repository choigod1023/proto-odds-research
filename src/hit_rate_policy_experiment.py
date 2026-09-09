"""Fixed, research-only recommendation ranking from immutable cached probabilities.

No fitting/search on evaluation labels. Primary comparison: one pick per KST
league/day from identical production-eligible pools. Candidate ranks agreement
first, then min(market, frozen process probability). Score is NOT a calibrated
probability or confidence bound. Reused K1 evaluation is exploratory only.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import subprocess

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
MODES = ('outcome', 'rolling-outcome', 'favorite-outcome')
PROTOCOL = {
    'objective': 'actual settled recommendation/game hit rate, not PA or Brier',
    'candidate_cache': 'outcome; frozen outcome fit through 2024-12-30',
    'selection': 'one fixed candidate; no hyperparameter or model selection',
    'ranking': 'agreement first, min(market, process) descending, market descending, kickoff and ID',
    'primary_budget': 'one per KST league/day, both arms identical eligible pool',
    'eligibility': 'real market-only frontend eligibility, 55% floor, preferred odds tier',
    'evaluation': '2025 onward, reused K1, exploratory, not independent confirmation',
    'production_allowed': False,
}


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt


def bucket(row):
    return row['league'], stamp(row['kickoff_at']).astimezone(KST).date().isoformat()


def validate(data, report):
    rows, test = data['rows'], data['test_rows']
    if report['training_mode'] != 'frozen_through2024':
        raise ValueError('primary cache must use frozen training')
    train = [r for r in rows if r['kickoff'][:10] <= '2024-12-30']
    if not train or not test or max(stamp(r['kickoff']) for r in train) >= min(stamp(r['kickoff']) for r in test):
        raise ValueError('train/test chronology')
    if len(train) != report['train_games']:
        raise ValueError('training count mismatch')
    ids = [(r['league'], r['event_id']) for r in test]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate event')
    expected = [r for r in rows if r['kickoff'][:4] >= '2025']
    if test != expected:
        raise ValueError('test membership mismatch')
    for row in rows:
        kickoff = stamp(row['kickoff'])
        if stamp(row['feature_as_of']) > kickoff - timedelta(minutes=30):
            raise ValueError('feature after cutoff')
        if row.get('odds_as_of') and stamp(row['odds_as_of']) > kickoff - timedelta(minutes=30):
            raise ValueError('odds after cutoff')
        if row['process_latest_allowed_date'] > str(kickoff.date() - timedelta(days=2)):
            raise ValueError('process history after cutoff')
    for name in ('market', 'process', 'market_calibration'):
        probabilities = data['probabilities'][name]
        if len(probabilities) != len(test):
            raise ValueError('probability alignment')
        for p in probabilities:
            if len(p) != 3 or any(not math.isfinite(v) or not 0 < v < 1 for v in p) or abs(sum(p)-1) > 1e-8:
                raise ValueError('invalid probability')
    for row in test:
        if len(row['odds']) != 3 or any(not math.isfinite(v) or v <= 1 for v in row['odds']):
            raise ValueError('invalid odds')
        if type(row['target']) is not int or not 0 <= row['target'] < 3:
            raise ValueError('invalid target')
    return train


def payload(data):
    # Explicit whitelist keeps outcomes/features out of both selectors.
    result = []
    for row, market, process in zip(data['test_rows'], data['probabilities']['market'], data['probabilities']['process']):
        favorite = max(range(3), key=lambda i: process[i])
        result.append([{
            'event_key': row['event_id'], 'league': row['league'],
            'kickoff_at': row['kickoff'], 'market': '승무패',
            'selection_id': row['event_id'] + '|' + str(i), 'choice': i,
            'odds': row['odds'][i], 'market_prob': market[i],
            'predicted_hit_prob': market[i], 'process_prob': process[i],
            'agreement': i == favorite,
        } for i in range(3)])
    return result


def rank(row, candidate):
    tail = (-row['market_prob'], row['kickoff_at'], row['selection_id'])
    return ((-int(row['agreement']), -min(row['market_prob'], row['process_prob'])) + tail
            if candidate else tail)


def select(pool, candidate=False, quotas=None):
    groups = defaultdict(list)
    for row in pool:
        groups[bucket(row)].append(row)
    return [r for key in sorted(groups) for r in sorted(groups[key], key=lambda r: rank(r, candidate))[:
            1 if quotas is None else quotas.get(key, 0)]]


def settle(picks, targets):
    return [{**r, 'hit': int(r['choice'] == targets[(r['league'], r['event_key'])])} for r in picks]


def stats(picks, games):
    n = len(picks)
    return {'n': n, 'hits': sum(r['hit'] for r in picks),
            'hit_rate': sum(r['hit'] for r in picks)/n if n else None,
            'coverage': n/games if games else None,
            'mean_odds': sum(r['odds'] for r in picks)/n if n else None}


def interval(left, right, draws=2000):
    # Paired league/day blocks, equal daily budgets; re-sample dates jointly.
    days = defaultdict(lambda: [0, 0])
    counts = [defaultdict(int), defaultdict(int)]
    for arm, rows in enumerate((left, right)):
        for row in rows:
            counts[arm][bucket(row)] += 1
            days[bucket(row)[1]][0] += row['hit'] * (1 if arm else -1)
            if arm == 0:
                days[bucket(row)[1]][1] += 1
    if counts[0] != counts[1]:
        raise ValueError('unmatched daily recommendation counts')
    if not days:
        return None
    blocks = [days[d] for d in sorted(days)]
    rng = random.Random(20260909)
    estimates = []
    for _ in range(draws):
        sampled = rng.choices(blocks, k=len(blocks))
        estimates.append(sum(b[0] for b in sampled)/sum(b[1] for b in sampled))
    estimates.sort()
    return {'hit_rate_delta': sum(b[0] for b in blocks)/sum(b[1] for b in blocks),
            'ci95_descriptive': [estimates[int(draws*.025)], estimates[int(draws*.975)]],
            'date_blocks': len(blocks), 'draws': draws}


def compare(left, right, games):
    keys = [('all', 'all')] + [(league, year) for league in sorted({r['league'] for r in games})
                              for year in ['all'] + sorted({r['kickoff'][:4] for r in games if r['league'] == league})]
    output = {}
    for league, year in keys:
        matches = lambda r: (league == 'all' or r['league'] == league) and (year == 'all' or r['kickoff_at'].startswith(year))
        a, b = [r for r in left if matches(r)], [r for r in right if matches(r)]
        n = sum((league == 'all' or r['league'] == league) and (year == 'all' or r['kickoff'].startswith(year)) for r in games)
        output[f'{league}/{year}'] = {'games': n, 'market': stats(a, n), 'agreement': stats(b, n),
            'paired': interval(a, b), 'changed_picks': len({r['selection_id'] for r in b} - {r['selection_id'] for r in a})}
    return output


def run(cache_root, output, report_path, node='node'):
    sources = {name: cache_root/name/'result.sqlite3' for name in MODES}
    before = {name: sha(path) for name, path in sources.items()}
    caches = {}
    for name, path in sources.items():
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-journal')):
            raise ValueError('source has journal; require stable snapshot')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            records = db.execute('SELECT status,metadata,report,data FROM run').fetchall()
        if len(records) != 1 or records[0][0] != 'complete':
            raise ValueError('incomplete cache')
        caches[name] = tuple(json.loads(v) for v in records[0][1:])
    meta, prior, data = caches['outcome']
    train = validate(data, prior)
    if any(other[2]['test_rows'] != data['test_rows'] for other in caches.values()):
        raise ValueError('prior caches use different evaluation rows')
    proc = subprocess.run([node, str(ROOT/'scripts/hit_rate_policy_bridge.mjs')],
        input=json.dumps(payload(data)), text=True, encoding='utf-8', capture_output=True, check=True)
    policy = json.loads(proc.stdout)
    pool = policy['eligible']
    primary = [select(pool, arm) for arm in (False, True)]
    quotas = defaultdict(int)
    for row in policy['production']:
        quotas[bucket(row)] += 1
    # Secondary control preserves the production budget and mandatory >=60% picks.
    # With <=3 eligible games (usual K1 slate), ranking cannot change membership.
    full = [policy['production']]
    candidate_full = []
    groups = defaultdict(list)
    for row in pool:
        groups[bucket(row)].append(row)
    for key, rows in sorted(groups.items()):
        strong = [r for r in rows if r['market_prob'] >= .60]
        weak = [r for r in rows if r['market_prob'] < .60]
        candidate_full.extend(strong + sorted(weak, key=lambda r: rank(r, True))[:max(0, quotas[key]-len(strong))])
    full.append(candidate_full)
    targets = {(r['league'], r['event_id']): r['target'] for r in data['test_rows']}
    primary = [settle(p, targets) for p in primary]
    full = [settle(p, targets) for p in full]
    result = {'protocol': PROTOCOL, 'source_sha256': before, 'prior_metadata': {k:v[0] for k,v in caches.items()},
        'prior_results': {k:v[1]['groups'] for k,v in caches.items()},
        'train_n': len(train), 'train_end': max(r['kickoff'] for r in train),
        'test_start': min(r['kickoff'] for r in data['test_rows']), 'test_end': max(r['kickoff'] for r in data['test_rows']),
        'eligible_n': len(pool), 'eligible_days': len(groups),
        'primary': compare(*primary, data['test_rows']), 'production_budget': compare(*full, data['test_rows']),
        'known_odds_time_n': sum(bool(r.get('odds_as_of')) for r in data['test_rows']),
        'code_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), ROOT/'scripts/hit_rate_policy_bridge.mjs',
            *sorted((ROOT/'web/src/lib').glob('*.js'))]}}
    after = {name: sha(path) for name, path in sources.items()}
    if before != after:
        raise ValueError('source hash changed')
    result['source_hash_unchanged'] = True
    if output.exists() or report_path.exists():
        raise ValueError('refuse overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output)) as db, db:
        db.execute('CREATE TABLE run (status TEXT, report TEXT, picks TEXT)')
        db.execute('INSERT INTO run VALUES (?,?,?)', ('complete', json.dumps(result), json.dumps({'primary': primary, 'production_budget': full})))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown(result), encoding='utf-8')
    return result


def markdown(result):
    lines = ['# Fixed recommendation hit-rate experiment — 2026-09-09', '',
        'Objective: actual settled recommendation/game hit rate. PA accuracy, shot error and Brier are not substitutes.', '',
        'Exploratory only: the K1 2025–2026 evaluation was reused by earlier experiments; there is no independent confirmation or production authorization.', '',
        '## Frozen protocol', '',
        'One new candidate uses the cached `outcome` process probabilities (outcome fit frozen through 2024). No models were rebuilt, no evaluation labels selected weights, thresholds, budgets or cache variants. The earlier rolling and favorite caches are read only for provenance and complete prior-result disclosure.', '',
        'Both arms use the real frontend market-favorite eligibility, market probability >=55%, and the same preferred-odds daily tier. The candidate ranks model/market direction agreement first, then min(market, process), then market probability, kickoff and stable ID. This minimum is a ranking score, not a calibrated probability or statistical lower bound.', '',
        'Primary: fixed one recommendation per eligible KST league/day in each arm. This deliberately changes the production volume; comparison against market uses that SAME one-pick budget. Secondary: preserve the full production count and mandatory market >=60% picks, rerank only remaining slots. No outcome-dependent count matching is used. Labels are attached only after both selectors finish.', '',
        f"Training: {result['train_n']} games through {result['train_end']}. Evaluation: {result['test_start']} to {result['test_end']}. Eligible: {result['eligible_n']} games across {result['eligible_days']} league/days.", '',
        '## Actual results', '']
    for mode in ('primary', 'production_budget'):
        lines += [f'### {mode}', '']
        for group, value in result[mode].items():
            a, b, ci = value['market'], value['agreement'], value['paired']
            if not a['n']:
                lines.append(f'- {group}: no recommendations.')
                continue
            lines.append(f"- {group}: market {a['hits']}/{a['n']} ({a['hit_rate']:.2%}); candidate {b['hits']}/{b['n']} ({b['hit_rate']:.2%}); delta {100*ci['hit_rate_delta']:+.2f} pp; descriptive 95% date-bootstrap [{100*ci['ci95_descriptive'][0]:+.2f}, {100*ci['ci95_descriptive'][1]:+.2f}] pp. Both coverage {a['coverage']:.2%} of {value['games']} games; mean odds {a['mean_odds']:.3f}/{b['mean_odds']:.3f}; changed picks {value['changed_picks']}.")
        lines.append('')
    lines += ['## All prior proposals (not selected as new winners)', '']
    for name, groups in result['prior_results'].items():
        for year, g in groups.items():
            a, b = g['policy']['market'], g['policy']['process']
            lines.append(f"- {name}, K1/{year}: prior unmatched production replay market {a['hits']}/{a['n']}, process {b['hits']}/{b['n']}; these differing counts are not evidence of improvement.")
    lines += ['', '## Limitations and decision', '',
        'No promotion. Any observed increase is descriptive and must be confirmed on newly collected, timestamp-verified future recommendations. A zero change means this ranking did not change the actual selected games; a decrease is retained as a failed result. Only K1 winner markets exist in these caches; no other league or full-site generalization is supported.', '',
        'Coverage denominator is all 330 cached evaluation games, not all scheduled K1 games or site markets. Equal per-day recommendation counts do not imply identical picks or identical odds; mean odds are disclosed. Bootstrap intervals are unadjusted for repeated exploration and do not repair data reuse. Chronological code checks cannot verify historical data availability.', '',
        f"Known archived odds capture timestamps: {result['known_odds_time_n']}; actual publication timing of the reconstructed process inputs is unverified. Within-day slate ranking assumes pregame signals available for the full slate, which these archives cannot prove. Upstream process forecasts update from prior games with D-2 cutoff; frozen refers to the outcome mapping, not permanently frozen team histories.", '',
        'All three source SQLite files were opened read-only and their SHA-256 hashes match before/after. Private per-pick outputs and full prior reports are stored only in ignored SQLite. No network data collection, production, scheduler, deployment or merge changes.', '',
        '## Reproduce', '', '```powershell',
        'python src/hit_rate_policy_experiment.py --cache-root ../dynamic-count-experiment-20260909/outputs/dynamic-count --output outputs/hit-rate-policy/result.sqlite3 --report experiments/hit-rate-policy-20260909.md',
        'python -m unittest discover -s tests -p test_hit_rate_policy_experiment.py -v', '```', '',
        'Choose unused output/report paths when rerunning. Reruns are not independent experiments.', '', 'Source hashes:', '']
    lines += [f'- {k}: `{v}`' for k,v in result['source_sha256'].items()]
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    result = run(args.cache_root, args.output, args.report, args.node)
    print(json.dumps({'primary': result['primary'], 'production_budget': result['production_budget'], 'source_hash_unchanged': True}))
