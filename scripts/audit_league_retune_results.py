"""Independently settle every saved forecast and reproduce validation-only selection."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

from retune_pair_audit import audit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from devig import market_probabilities

DEFAULT = ROOT / 'experiments/baseball-league-retune-20261029'
HEADS = ('primary', 'hit_leader', 'profit_leader')


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def score(records, name):
    return audit([dict(r, candidate_probability=r['candidate_probabilities'][name]) for r in records])


def selection(records):
    names = set(records[0]['candidate_probabilities'])
    if any(set(r['candidate_probabilities']) != names for r in records):
        raise ValueError('candidate coverage differs')
    metrics = {n: score(records, n)['candidate'] for n in names}
    base = metrics['market_null']
    eligible = [n for n, m in metrics.items() if m['accuracy'] >= base['accuracy']
                and m['net_krw'] >= base['net_krw']]
    chosen = dict(
        primary=min(eligible, key=lambda n: (metrics[n]['logloss'], metrics[n]['brier'], n)),
        hit_leader=min(names, key=lambda n: (-metrics[n]['accuracy'], metrics[n]['brier'], n)),
        profit_leader=min(names, key=lambda n: (-metrics[n]['net_krw'], metrics[n]['logloss'], n)),
    )
    return chosen, metrics


def compare(actual, expected, context):
    for field in ('n', 'wins', 'pushes', 'accuracy', 'roi', 'net_krw', 'logloss', 'brier'):
        if not math.isclose(actual[field], expected[field], abs_tol=1e-10, rel_tol=1e-10):
            raise AssertionError((context, field, actual[field], expected[field]))


def verify_league(folder, league):
    result = read(folder/'results.json')
    frozen = read(folder/'frozen-selection.json')
    validation = rows(folder/'validation_predictions.jsonl')
    evaluation = rows(folder/'predictions.jsonl')
    source_votes = {r['gamePk']: r['target'] for r in rows(folder/'sources/secondary116.jsonl')} if league == 'mlb' else {}
    for row in validation + evaluation:
        market = market_probabilities(row['prices'])[row['order'].index('home')]
        if not math.isclose(market, row['market_probability'], abs_tol=1e-11):
            raise AssertionError('market reference mismatch')
        if row in validation or row['baseline_kind'] == 'market_only':
            reference = market
        else:
            votes = source_votes[row['gamePk']] if league == 'mlb' else row['frozen_votes']
            if votes['voters'] <= 0 or sum(votes['vote_counts'].values()) != votes['voters']:
                raise AssertionError('vote count mismatch')
            reference = .9*market + .1*votes['vote_counts'].get('home', 0)/votes['voters']
        if not math.isclose(reference, row['baseline_probability'], abs_tol=1e-11):
            raise AssertionError('frozen research 9:1 or market baseline mismatch')
    chosen, val_metrics = selection(validation)
    expected = result['selected'] if league == 'mlb' else result['heads']
    frozen_heads = frozen['selected'] if league == 'mlb' else frozen['heads']
    for head in HEADS:
        if chosen[head] != expected[head] or chosen[head] != frozen_heads[head]:
            raise AssertionError(('selection mismatch', league, head))
    if len(val_metrics) != result['candidate_count']:
        raise AssertionError('candidate count')
    for name, actual in val_metrics.items():
        expected_metrics = result['validation'][name] if league == 'mlb' else result['candidates'][name]['validation']
        compare(actual, expected_metrics, (league, 'validation', name))
    for records in (validation, evaluation):
        ids = [r.get('fixture_id', r.get('gamePk')) for r in records]
        if len(set(ids)) != len(ids):
            raise AssertionError('duplicate fixture')
        if any(set(r['candidate_probabilities']) != set(val_metrics) for r in records):
            raise AssertionError('evaluation coverage mismatch')
    groups = defaultdict(list)
    for row in evaluation:
        groups[row['eval_set'] if league == 'mlb' else 'kbo198'].append(row)
        policies = row['policies'] if league == 'mlb' else row
        for head in HEADS:
            policy = policies[head]
            if policy['candidate'] != chosen[head] or policy['probability'] != row['candidate_probabilities'][chosen[head]]:
                raise AssertionError('head forecast is not frozen candidate')
    output = {}
    for group, records in groups.items():
        for name in val_metrics:
            actual = score(records, name)['candidate']
            expected_metrics = result['evaluation'][group][name] if league == 'mlb' else result['candidates'][name]['evaluation']
            compare(actual, expected_metrics, (league, group, name))
        split = '2026-08-10' if group == 'external230' else '2026-07-01'
        panels = {'all': records, 'before_split': [r for r in records if r['date'] < split],
                  'split_onward': [r for r in records if r['date'] >= split]}
        output[group] = {}
        for period, panel in panels.items():
            output[group][period] = {head: score(panel, chosen[head]) for head in HEADS}
            source = result['periods'][group]['periods'][period] if league == 'mlb' else result['periods'][{
                'all': 'all', 'before_split': 'before_july', 'split_onward': 'july_onward'}[period]]
            for head, actual in output[group][period].items():
                compare(actual['baseline'], source['baseline'], (league, group, period, 'baseline'))
                saved = source['policies' if league == 'mlb' else 'heads'][head]
                compare(actual['candidate'], saved['metrics'], (league, group, period, head))
                if actual['pick_changes'] != saved['pick_changes' if league == 'mlb' else 'changes_vs_baseline']:
                    raise AssertionError('pick change mismatch')
    hashes = {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
              for p in (folder/'results.json', folder/'frozen-selection.json',
                        folder/'predictions.jsonl', folder/'validation_predictions.jsonl')}
    return dict(selected=chosen, validation_rows=len(validation), candidate_count=len(val_metrics),
                evaluation_rows=len(evaluation), all_saved_candidate_metrics_match=True,
                market_and_frozen_vote_reference_match=True,
                input_hashes=hashes, panels=output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEFAULT)
    args = parser.parse_args()
    results = {league: verify_league(args.folder/league, league) for league in ('mlb', 'kbo')}
    output = dict(ok=True, settlement='single fixed KRW10000; decimal payout truncation; no monthly cap',
                  leagues=results)
    (args.folder/'independent-result-audit.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    for league, result in results.items():
        print(league, result['candidate_count'], result['validation_rows'], result['evaluation_rows'], result['selected'])


if __name__ == '__main__':
    main()
