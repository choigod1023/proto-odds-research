"""Bounded retrospective odds-only tournament. Frozen protocol a270079e.

CSV parsing/download adapted from PR256 evaluate_draw_calibration_holdout.py;
no runtime dependency on another worktree and no production data access.
"""
from __future__ import annotations

import os
for _name in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
    os.environ[_name] = '1'

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
import hashlib
import io
from itertools import product
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

import numpy as np
import scipy
from scipy.optimize import minimize, minimize_scalar
from scipy.special import softmax, logsumexp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.devig import multiplicative, power, shin

REV = 'e82cf59161e82e0fbcdc49ad02819e45c821a5c8'
LEAGUES = {'D1': 'bundesliga1', 'SP1': 'laliga'}
PROTOCOL = 'docs/research/2026-10-01-local-tournament-protocol.md'
PROTOCOL_COMMIT = 'a270079e'
MODEL_IDS = ('multiplicative', 'power', 'shin', 'temperature', 'bias_0.01', 'bias_0.1',
             'linear_0.01', 'linear_0.1', 'blend_0.25', 'blend_0.5')
SEED, BOOTSTRAPS = 20261001, 5000


def parse_source(raw, league, year):
    """Adapted from PR256: strict complete-season checks, outcomes only for labels."""
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if not r.get('HomeTeam'):
            continue
        if r.get('Div') != league:
            raise ValueError('League mismatch')
        day = datetime.strptime(r['Date'], '%d/%m/%Y' if len(r['Date'].split('/')[-1]) == 4 else '%d/%m/%y').date()
        if not datetime(2000+year, 7, 1).date() <= day < datetime(2001+year, 7, 1).date():
            if not (year == 19 and day.year == 2020 and day < datetime(2020, 9, 1).date()):
                raise ValueError('Season/date mismatch')
        goals = int(r['FTHG']), int(r['FTAG'])
        y = 0 if goals[0] > goals[1] else 1 if goals[0] == goals[1] else 2
        if min(goals) < 0 or r['FTR'] != 'HDA'[y]:
            raise ValueError('Invalid result')
        try:
            prices = [float(r['B365'+s]) for s in 'HDA']
            if any(not np.isfinite(v) or v <= 1 for v in prices):
                prices = None
        except (KeyError, TypeError, ValueError):
            prices = None
        rows.append(dict(match_id=f"{league}:{day}:{r['HomeTeam']}:{r['AwayTeam']}",
                         league=league, season=year, date=day.isoformat(), prices=prices, y=y))
    if len(rows) != (306 if league == 'D1' else 380) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError(f'Coverage/duplicate failure {league}/{year}')
    return rows


def download():
    def one(item):
        league, year = item
        url = f'https://raw.githubusercontent.com/sosthene14/footballdataset/{REV}/datasets/{LEAGUES[league]}/{year:02}{year+1:02}_{league}.csv'
        raw = urllib.request.urlopen(url, timeout=30).read()
        rows = parse_source(raw, league, year)
        return rows, dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), matches=len(rows),
                          missing_prices=sum(r['prices'] is None for r in rows))
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(one, product(LEAGUES, range(19, 25))))
    return sorted([r for rows, _ in parts for r in rows], key=lambda r: (r['date'], r['match_id'])), [s for _, s in parts]


def split_fold(rows, outer_season):
    if outer_season not in (23, 24) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Invalid fold/duplicate')
    if any(r['league'] not in LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unknown source member')
    parts = [[r for r in rows if r['season'] < outer_season-1],
             [r for r in rows if r['season'] == outer_season-1],
             [r for r in rows if r['season'] == outer_season]]
    if any(not part for part in parts):
        raise ValueError('Empty temporal split')
    for a, b in zip(parts, parts[1:]):
        if max(r['date'] for r in a) >= min(r['date'] for r in b):
            raise ValueError('Date batch leakage/overlap')
    return parts


def valid(p):
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or (p <= 0).any() or not np.allclose(p.sum(1), 1):
        raise ValueError('Invalid probability matrix')
    return p


def market(rows, method=shin):
    return valid([method(r['prices']) for r in rows])


def nll(logits, y):
    return float(np.mean(logsumexp(logits, axis=1)-logits[np.arange(len(y)), y]))


def fit_models(train, before):
    """No validation/test argument. before is the earliest permitted prediction date."""
    if not train or any(r['date'] >= before for r in train):
        raise ValueError('Training dates must strictly precede prediction batch')
    z = np.log(market(train))
    y = np.array([r['y'] for r in train])
    target = np.eye(3)[y]
    temp = minimize_scalar(lambda t: nll(z/t, y), bounds=(.5, 2.), method='bounded')
    if not temp.success:
        raise RuntimeError('Temperature fit failed')
    models = {name: {} for name in MODEL_IDS}
    models['temperature'] = dict(temperature=float(temp.x), converged=True)
    for family, penalty in product(('bias', 'linear'), (.01, .1)):
        x = np.ones((len(train), 1)) if family == 'bias' else np.column_stack([np.ones(len(train)), z])
        def objective(flat):
            w = flat.reshape(x.shape[1], 3)
            logits = z+x@w
            return nll(logits, y)+penalty*np.square(w).sum()/2, (x.T@(softmax(logits, axis=1)-target)/len(y)+penalty*w).ravel()
        fitted = minimize(objective, np.zeros(x.shape[1]*3), jac=True, method='L-BFGS-B', options={'maxiter': 300, 'ftol': 1e-12})
        if not fitted.success:
            raise RuntimeError(str(fitted.message))
        models[f'{family}_{penalty}'] = dict(weights=fitted.x.reshape(x.shape[1], 3).tolist(),
                                            converged=True, iterations=int(fitted.nit))
    return models


def predict_models(models, rows):
    """Reads prices only, never results; fitted parameters fixed for full season."""
    p = market(rows)
    z = np.log(p)
    predictions = dict(multiplicative=market(rows, multiplicative), power=market(rows, power), shin=p,
                       temperature=softmax(z/models['temperature']['temperature'], axis=1))
    for family, penalty in product(('bias', 'linear'), (.01, .1)):
        x = np.ones((len(rows), 1)) if family == 'bias' else np.column_stack([np.ones(len(rows)), z])
        name = f'{family}_{penalty}'
        predictions[name] = softmax(z+x@np.asarray(models[name]['weights']), axis=1)
    for alpha in (.25, .5):
        predictions[f'blend_{alpha}'] = (1-alpha)*p+alpha*predictions['linear_0.01']
    return {k: valid(v) for k, v in predictions.items()}


def grid():
    return [dict(id=f'{m}/{s}/{t:.2f}/{r}', model=m, scope=s, threshold=t, rank=r)
            for m, s, t, r in product(MODEL_IDS, ('all', 'favorite', 'underdog', 'draw'), (0., .02, .05), ('maxprob', 'ev'))]


BASELINE = dict(id='baseline', model='shin', scope='all', threshold=None, rank='maxprob')
SECONDARY = dict(id='p60_range_surrogate', model='shin', scope='all', threshold=None, rank='maxprob', p60_range=True)


def choose(rows, p, policy):
    p = valid(p)
    if len(rows) != len(p) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Selection shape/duplicate')
    days = defaultdict(list)
    for row, probs in zip(rows, p):
        odds = row['prices']
        if len(odds) != 3 or any(not np.isfinite(o) or o <= 1 for o in odds):
            raise ValueError('Invalid odds')
        key = (row['league'], row['date'])
        days[key]
        options = []
        for i, (prob, price) in enumerate(zip(probs, odds)):
            scope = policy['scope']
            if not {'all': True, 'favorite': price == min(odds), 'underdog': price > min(odds), 'draw': i == 1}[scope]:
                continue
            if policy['threshold'] is not None and prob*price-1 < policy['threshold']-1e-12:
                continue
            if policy.get('p60_range') and not (prob >= .6 and 1.5 <= price < 2.2):
                continue
            score = prob if policy['rank'] == 'maxprob' else prob*price
            options.append(dict(id=row['match_id'], choice=i, probability=float(prob), odds=price, score=float(score)))
        if options:
            days[key].append(min(options, key=lambda a: (-a['score'], a['choice'])))
    return {key: sorted(options, key=lambda a: (-a['score'], a['id'], a['choice']))[:2] if len(options) >= 2 else []
            for key, options in sorted(days.items())}


def settle(all_rows, tickets):
    actual = {r['match_id']: r for r in all_rows}
    output = []
    for league, day in sorted({(r['league'], r['date']) for r in all_rows}, key=lambda k: (k[1], k[0])):
        pair = tickets.get((league, day), [])
        if pair and (len(pair) != 2 or len({a['id'] for a in pair}) != 2):
            raise ValueError('Requires two distinct games')
        if any((actual[a['id']]['league'], actual[a['id']]['date']) != (league, day) for a in pair):
            raise ValueError('Cross-budget ticket')
        win = int(bool(pair) and all(a['choice'] == actual[a['id']]['y'] for a in pair))
        stake = int(bool(pair))
        output.append(dict(league=league, date=day, budget=1, stake=stake, wins=win,
                           profit=win*float(np.prod([a['odds'] for a in pair]))-stake,
                           predicted=float(np.prod([a['probability'] for a in pair])) if pair else 0., pair=pair))
    return output


def summary(records):
    stake = sum(r['stake'] for r in records)
    profit = sum(r['profit'] for r in records)
    wins = sum(r['wins'] for r in records)
    signature = [(r['league'], r['date'], [(a['id'], a['choice']) for a in r['pair']]) for r in records]
    legs = [a for r in records for a in r['pair']]
    return dict(budgets=len(records), tickets=stake, stake=stake, cash=len(records)-stake, wins=wins,
                coverage=stake/len(records), hit_rate=wins/stake if stake else None,
                profit=profit, roi=profit/stake if stake else None, budget_return=profit/len(records),
                mean_predicted_pair=sum(r['predicted'] for r in records)/stake if stake else None,
                low_odds_legs=sum(a['odds'] < 1.5 for a in legs),
                selection_sha256=hashlib.sha256(json.dumps(signature, separators=(',', ':')).encode()).hexdigest())


def probability_records(rows, p):
    output = []
    for row, probs in zip(rows, p):
        choice = int(np.argmax(probs))
        win = int(choice == row['y'])
        output.append(dict(date=row['date'], league=row['league'], n=1, ll=float(-np.log(probs[row['y']])),
                           brier=float(np.square(probs-np.eye(3)[row['y']]).sum()), accuracy=win,
                           single_profit=win*row['prices'][choice]-1, odds=row['prices'][choice]))
    return output


def probability_summary(rows, p):
    records = probability_records(rows, p)
    return dict(matches=len(records), logloss=float(np.mean([r['ll'] for r in records])),
                brier=float(np.mean([r['brier'] for r in records])),
                argmax_accuracy=float(np.mean([r['accuracy'] for r in records])),
                single_pick=dict(picks=len(records), wins=sum(r['accuracy'] for r in records),
                                 roi=float(np.mean([r['single_profit'] for r in records])),
                                 profit=float(sum(r['single_profit'] for r in records)),
                                 mean_odds=float(np.mean([r['odds'] for r in records])),
                                 low_odds_fraction=float(np.mean([r['odds'] < 1.5 for r in records]))))


def choose_winners(inner_trials, inner_models):
    if any(t.get('stage') != 'inner' for t in inner_trials):
        raise ValueError('Winner selection accepts inner trials only')
    eligible = [t for t in inner_trials if t['summary']['tickets'] >= 20]
    policy = min(eligible, key=lambda t: (-t['summary']['budget_return'], inner_models[t['config']['model']]['logloss'], t['config']['id']))['config'] if eligible else BASELINE.copy()
    model = min(inner_models, key=lambda m: (inner_models[m]['logloss'], m))
    return dict(policy=policy, probability_model=model, eligible_trials=len(eligible))


def paired_ci(base, challenger, numerator, denominator):
    if [(r['date'], r['league']) for r in base] != [(r['date'], r['league']) for r in challenger]:
        raise ValueError('Unpaired records')
    weekly = defaultdict(lambda: np.zeros(4))
    for a, b in zip(base, challenger):
        key = tuple(datetime.fromisoformat(a['date']).isocalendar()[:2])
        weekly[key] += [a[numerator], a[denominator], b[numerator], b[denominator]]
    matrix = np.array([weekly[k] for k in sorted(weekly)])
    rng = np.random.default_rng(SEED)
    values = matrix[rng.integers(len(matrix), size=(BOOTSTRAPS, len(matrix)))].sum(1)
    total = matrix.sum(0)
    def estimate(n, d):
        ok = values[:, d] > 0
        sample = values[ok, n]/values[ok, d]
        return dict(value=float(total[n]/total[d]) if total[d] else None,
                    ci95=np.quantile(sample, [.025, .975]).tolist() if len(sample) else None,
                    valid_bootstraps=int(ok.sum()))
    ok = (values[:, 1] > 0)&(values[:, 3] > 0)
    delta = values[ok, 2]/values[ok, 3]-values[ok, 0]/values[ok, 1]
    return dict(base=estimate(0, 1), challenger=estimate(2, 3), weeks=len(matrix),
                delta=dict(value=float(total[2]/total[3]-total[0]/total[1]) if total[1] and total[3] else None,
                           ci95=np.quantile(delta, [.025, .975]).tolist() if len(delta) else None,
                           valid_bootstraps=int(ok.sum())))


def policy_inference(base, challenger):
    return {name: paired_ci(base, challenger, numerator, denominator)
            for name, numerator, denominator in [('budget_return', 'profit', 'budget'), ('roi', 'profit', 'stake'), ('hit_rate', 'wins', 'stake')]}


def curve(records):
    total = 0.
    output = []
    for r in records:
        total += r['profit']
        output.append(dict(date=r['date'], league=r['league'], cumulative_profit=total))
    return output


def evaluate_trials(stage, all_rows, rows, predictions):
    pregame = [{k: r[k] for k in ('match_id', 'league', 'date', 'prices')} for r in rows]
    return [dict(stage=stage, config=config, summary=summary(settle(all_rows, choose(pregame, predictions[config['model']], config))))
            for config in grid()]


def run():
    started = time.perf_counter()
    frozen = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL}'], cwd=ROOT)
    if frozen.replace(b'\r\n', b'\n') != (ROOT/PROTOCOL).read_bytes().replace(b'\r\n', b'\n'):
        raise ValueError('Protocol changed after freeze')
    raw, sources = download()
    folds = []
    pooled = {name: [] for name in ('baseline', 'selected', 'secondary')}
    pooled_probs = {name: [] for name in ('baseline', 'selected')}
    for outer in (23, 24):
        train_all, inner_all, outer_all = split_fold(raw, outer)
        train, inner, test = [[r for r in rs if r['prices'] is not None] for rs in (train_all, inner_all, outer_all)]
        models = fit_models(train, min(r['date'] for r in inner_all))
        inner_predictions = predict_models(models, inner)
        inner_metrics = {m: probability_summary(inner, p) for m, p in inner_predictions.items()}
        inner_trials = evaluate_trials('inner', inner_all, inner, inner_predictions)
        winners = choose_winners(inner_trials, inner_metrics)
        print(f"fold {outer}: locked inner policy={winners['policy']['id']}, probability={winners['probability_model']}", flush=True)
        refitted = fit_models(train+inner, min(r['date'] for r in outer_all))
        outer_predictions = predict_models(refitted, test)
        outer_metrics = {m: probability_summary(test, p) for m, p in outer_predictions.items()}
        outer_trials = evaluate_trials('outer_exploratory', outer_all, test, outer_predictions)
        pregame = [{k: r[k] for k in ('match_id', 'league', 'date', 'prices')} for r in test]
        records = {name: settle(outer_all, choose(pregame, outer_predictions[cfg['model']], cfg))
                   for name, cfg in [('baseline', BASELINE), ('selected', winners['policy']), ('secondary', SECONDARY)]}
        prob_records = {name: probability_records(test, outer_predictions[m]) for name, m in [('baseline', 'shin'), ('selected', winners['probability_model'])]}
        for name, rs in records.items():
            pooled[name].extend(rs)
        for name, rs in prob_records.items():
            pooled_probs[name].extend(rs)
        folds.append(dict(outer_season=outer, split={name: dict(raw_matches=len(rs), priced_matches=sum(r['prices'] is not None for r in rs), first=min(r['date'] for r in rs), last=max(r['date'] for r in rs))
                                                    for name, rs in [('train', train_all), ('inner', inner_all), ('outer', outer_all)]},
                          winners=winners, training_parameters=models, refit_parameters=refitted,
                          inner_models=inner_metrics, outer_models=outer_metrics, inner_trials=inner_trials, outer_trials=outer_trials,
                          policy={name: summary(rs) for name, rs in records.items()},
                          inference=policy_inference(records['baseline'], records['selected']),
                          probability_inference={metric: paired_ci(prob_records['baseline'], prob_records['selected'], metric, 'n') for metric in ('ll', 'brier', 'accuracy')},
                          records=records, curves={name: curve(rs) for name, rs in records.items()}))
    return dict(protocol_commit=PROTOCOL_COMMIT, source_revision=REV, sources=sources,
                code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                dependency_sha256={'src/devig.py': hashlib.sha256((ROOT/'src/devig.py').read_bytes()).hexdigest()},
                environment=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, blas_threads=1),
                grid=dict(unique_trials=len(grid()), models=10, policies=24, folds=2, total_trial_evaluations=960,
                          baselines=[BASELINE, SECONDARY], seed=SEED, bootstraps=BOOTSTRAPS),
                folds=folds, pooled=dict(policy={name: summary(rs) for name, rs in pooled.items()},
                                        inference=policy_inference(pooled['baseline'], pooled['selected']),
                                        secondary_inference=policy_inference(pooled['baseline'], pooled['secondary']),
                                        probability_inference={metric: paired_ci(pooled_probs['baseline'], pooled_probs['selected'], metric, 'n') for metric in ('ll', 'brier', 'accuracy')},
                                        curves={name: curve(rs) for name, rs in pooled.items()}),
                elapsed_seconds=time.perf_counter()-started,
                limitations=['Exposed data: exploratory retrospective nested walk-forward, not fresh confirmation.',
                             'Odds-only surrogate; not current production ROI. No actual Proto purchase prices.',
                             '240 comparisons; outer rankings exploratory; no White Reality Check.',
                             'Different games and weekly clusters do not establish outcome independence.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(elapsed_seconds=report['elapsed_seconds'], policy=report['pooled']['policy'],
                          paired_budget=report['pooled']['inference']['budget_return']['delta']), ensure_ascii=False))
