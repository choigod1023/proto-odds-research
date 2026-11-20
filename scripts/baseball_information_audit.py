"""Parent audit: settle both independent workers without importing their code."""
from collections import defaultdict
from decimal import Decimal, ROUND_DOWN
import argparse
import gzip
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'experiments/baseball-information-stepchange-20261029'


def read(path):
    raw = gzip.decompress(path.read_bytes()).decode('utf8') if path.suffix == '.gz' else path.read_text(encoding='utf8')
    return [json.loads(s) for s in raw.splitlines() if s]


def score(records, probabilities, reference):
    if not len(records) == len(probabilities) == len(reference) or not records:
        raise ValueError('empty or misaligned cohort')
    wins = pushes = net = changes = gained = lost = gain_payout = loss_payout = 0
    losses = brier = 0.
    daily = defaultdict(int)
    for r, p, b in zip(records, probabilities, reference):
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (p, b)):
            raise ValueError('invalid probability')
        side, old = ['home' if v >= .5 else 'away' for v in (p, b)]
        changes += side != old
        if r['hg'] == r['ag']:
            pushes += 1
            continue
        y = r['hg'] > r['ag']
        hit, was_hit = [(s == 'home') == y for s in (side, old)]
        wins += hit
        payout = int((Decimal(str(r['prices'][r['order'].index(side)]))*10000).to_integral_value(rounding=ROUND_DOWN))
        old_payout = int((Decimal(str(r['prices'][r['order'].index(old)]))*10000).to_integral_value(rounding=ROUND_DOWN))
        profit = payout-10000 if hit else -10000
        net += profit
        daily[r['date']] += profit
        if hit and not was_hit:
            gained += 1
            gain_payout += payout
        if was_hit and not hit:
            lost += 1
            loss_payout += old_payout
        brier += (p-int(y))**2
        p = min(1-1e-10, max(1e-10, p))
        losses -= math.log(p if y else 1-p)
    balance = peak = dd = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        dd = max(dd, peak-balance)
    return dict(n=len(records), wins=wins, pushes=pushes, accuracy=wins/(len(records)-pushes),
                net_krw=net, roi=net/(len(records)*10000), logloss=losses/(len(records)-pushes),
                brier=brier/(len(records)-pushes), pick_changes=changes,
                prediction_variation=dict(home_picks=sum(p >= .5 for p in probabilities),
                                          away_picks=sum(p < .5 for p in probabilities),
                                          minimum_home_probability=min(probabilities),
                                          maximum_home_probability=max(probabilities),
                                          identical_directions_to_all_home=all(p >= .5 for p in probabilities)),
                daily_close_max_drawdown_krw=dd,
                decomposition=dict(gained_wins=gained, lost_wins=lost, gained_payout_krw=gain_payout,
                                   lost_payout_krw=loss_payout, net_improvement_krw=gain_payout-loss_payout))


def compare(actual, expected, mlb=False):
    aliases = {'wins': 'correct', 'accuracy': 'hit'} if mlb else {}
    for k in ('n', 'wins', 'pushes', 'accuracy', 'net_krw', 'roi', 'logloss', 'brier'):
        if not math.isclose(actual[k], expected[aliases.get(k, k)], abs_tol=1e-10, rel_tol=0):
            raise AssertionError((k, actual[k], expected[aliases.get(k, k)]))
    if not mlb and actual['daily_close_max_drawdown_krw'] != expected['daily_close_max_drawdown_krw']:
        raise AssertionError('daily drawdown mismatch')


def match_reference(row, reference):
    for k in ('date', 'hg', 'ag', 'prices', 'order'):
        if row[k] != reference[k]:
            raise AssertionError(('cohort or price changed', k, row[k], reference[k]))


def audit(folder, mlb_folder=None, kbo_folder=None):
    mlb_folder = mlb_folder or folder/'mlb'
    kbo_folder = kbo_folder or folder/'kbo'
    original = {r['fixture_id']: r for league in ('mlb', 'kbo')
                for r in read(folder/'diagnostic'/(league+'-inputs.jsonl')) if r['role'] == 'evaluation'}
    out = dict(mlb={}, kbo={})
    m = json.loads((mlb_folder/'results.json').read_text(encoding='utf8'))
    records = read(mlb_folder/'predictions.jsonl')
    for r in records:
        r['order'] = r['price_order']
        prior = original['MLB:'+str(r['gamePk'])]
        match_reference(r, prior)
        if not math.isclose(r['probabilities']['baseline'], prior['baseline_probability'], abs_tol=1e-10):
            raise AssertionError('MLB baseline changed')
        if not r['announced'] and any(
                not math.isclose(r['probabilities'][name], r['probabilities']['baseline'], abs_tol=1e-12, rel_tol=0)
                or (r['probabilities'][name] >= .5) != (r['probabilities']['baseline'] >= .5)
                for name in ('primary', 'hit', 'primary_blend10', 'hit_blend10')):
            raise AssertionError('unannounced game used a prediction instead of reference fallback')
    if len({r['gamePk'] for r in records}) != len(records) or len(records) != 346:
        raise AssertionError('MLB cohort lost or duplicated')
    for label, expected in m['groups'].items():
        cohort, mode, period = label.split('/')
        group = [r for r in records if r['cohort'] == cohort and (mode == 'full_fallback' or r['announced'])]
        split = '2026-08-10' if cohort == 'external230' else '2026-07-01'
        if period == 'before_split':
            group = [r for r in group if r['date'] < split]
        elif period == 'split_onward':
            group = [r for r in group if r['date'] >= split]
        elif period.startswith('month_'):
            group = [r for r in group if r['date'].startswith(period[6:])]
        base = [r['probabilities']['baseline'] for r in group]
        actuals = {}
        for name, saved in expected.items():
            actuals[name] = score(group, [r['probabilities'][name] for r in group], base)
            compare(actuals[name], saved, mlb=True)
            if actuals[name]['pick_changes'] != saved['changed_picks']:
                raise AssertionError('MLB changed picks mismatch')
        if period in ('all', 'before_split', 'split_onward'):
            out['mlb'][label] = actuals
    k = json.loads((kbo_folder/'results.json').read_text(encoding='utf8'))
    records = read(kbo_folder/'evaluation-ledger.jsonl.gz')
    for r in records:
        prior = original[r['fixture_id']]
        match_reference(r, prior)
        if not math.isclose(r['baseline_probability'], prior['baseline_probability'], abs_tol=1e-10):
            raise AssertionError('KBO baseline changed')
    if len(records) != 198 or len({r['fixture_id'] for r in records}) != 198:
        raise AssertionError('KBO cohort lost or duplicated')
    for period, candidates in k['periods'].items():
        group = [r for r in records if period == 'all' or
                 (r['date'] < '2026-07-01' if period == 'pre_july' else r['date'] >= '2026-07-01')]
        base = [r['baseline_probability'] for r in group]
        out['kbo'][period] = {}
        baseline_net = score(group, base, base)['net_krw']
        for name, saved in candidates.items():
            ps = [r['candidates'][name]['probability'] for r in group]
            actual = score(group, ps, base)
            compare(actual, saved['metrics'])
            if actual['pick_changes'] != saved['changes_vs_baseline']:
                raise AssertionError('KBO changed picks mismatch')
            if actual['net_krw']-baseline_net != actual['decomposition']['net_improvement_krw']:
                raise AssertionError('KBO paired payout identity')
            out['kbo'][period][name] = actual
    return dict(ok=True, fixed_stake=10000, monthly_cap=False, independent_of_worker_code=True,
                reference_and_cohort_unchanged=True, evaluation=out)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder', type=Path, default=DEFAULT)
    p.add_argument('--mlb-folder', type=Path)
    p.add_argument('--kbo-folder', type=Path)
    args = p.parse_args()
    result = audit(args.folder, args.mlb_folder, args.kbo_folder)
    (args.folder/'independent-model-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print('Independent model audit passed; MLB groups:', len(result['evaluation']['mlb']), 'KBO periods:', len(result['evaluation']['kbo']))
