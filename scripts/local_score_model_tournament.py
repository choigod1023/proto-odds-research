"""Frozen exploratory score-model tournament; local research, never production.

Loader adapted from PR256 evaluate_draw_calibration_holdout.py (credited in
protocol). Self contained, no runtime imports from sibling worktrees.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
from itertools import combinations, product
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

for _thread_variable in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_thread_variable] = '1'

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.devig import shin

REV = 'e82cf59161e82e0fbcdc49ad02819e45c821a5c8'
LEAGUES = {'D1': 'bundesliga1', 'SP1': 'laliga'}
SEED, REPS = 20261001, 5000
PROTOCOL = ROOT / 'docs/research/2026-10-01-score-tournament-protocol.md'
PROTOCOL_COMMIT = '626a3db7'
POLICIES = ('highestprob', 'p60range', 'p60low', 'ev02', 'ev05')


def verify_protocol():
    committed = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(ROOT).as_posix()}'], cwd=ROOT)
    if PROTOCOL.read_bytes() != committed:
        raise ValueError('Frozen protocol bytes differ from explicit pre-run commit')
    return subprocess.check_output(['git', 'rev-parse', PROTOCOL_COMMIT], cwd=ROOT, text=True).strip()


def guarded_training(rows, last_season, evaluation):
    if not evaluation or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Empty evaluation or duplicate identities')
    first = min(r['date'] for r in evaluation)
    past = [r for r in rows if 19 <= r['season'] <= last_season]
    if not past or max(r['date'] for r in past) >= first:
        raise ValueError('Season/date temporal overlap')
    cutoff = (date.fromisoformat(first)-timedelta(days=7)).isoformat()
    training = [r for r in past if r['date'] <= cutoff]
    if not training or any(r['date'] > cutoff for r in training):
        raise ValueError('Seven-day fit guard violated')
    return training


def parse_source(raw, league, year):
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if not r.get('HomeTeam'):
            continue
        if r.get('Div') != league:
            raise ValueError('League mismatch')
        day = datetime.strptime(r['Date'], '%d/%m/%Y' if len(r['Date'].split('/')[-1]) == 4 else '%d/%m/%y').date()
        if not date(2000+year, 7, 1) <= day < date(2001+year, 7, 1):
            if not (year == 19 and day.year == 2020 and day < date(2020, 9, 1)):
                raise ValueError('Season/date mismatch')
        hg, ag = int(r['FTHG']), int(r['FTAG'])
        y = 0 if hg > ag else 1 if hg == ag else 2
        if min(hg, ag) < 0 or 'HDA'[y] != r['FTR']:
            raise ValueError('Invalid result')
        try:
            prices = [float(r['B365'+s]) for s in 'HDA']
            if any(not np.isfinite(v) or v <= 1 for v in prices):
                prices = None
        except (KeyError, ValueError, TypeError):
            prices = None
        rows.append(dict(match_id=f"{league}:{day}:{r['HomeTeam']}:{r['AwayTeam']}",
                         league=league, season=year, date=day.isoformat(), y=y,
                         home=r['HomeTeam'], away=r['AwayTeam'], hg=hg, ag=ag, prices=prices))
    if len(rows) != (306 if league == 'D1' else 380):
        raise ValueError(f'Coverage failure: {league}/{year}: {len(rows)}')
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate match')
    return rows


def load_sources():
    def one(item):
        league, year = item
        url = f'https://raw.githubusercontent.com/sosthene14/footballdataset/{REV}/datasets/{LEAGUES[league]}/{year:02}{year+1:02}_{league}.csv'
        raw = urllib.request.urlopen(url, timeout=30).read()
        rows = parse_source(raw, league, year)
        return rows, dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), matches=len(rows),
                          missing_prices=sum(r['prices'] is None for r in rows))
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(one, product(LEAGUES, range(19, 25))))
    return sorted([r for rows, _ in parts for r in rows], key=lambda r: (r['date'], r['match_id'])), [m for _, m in parts]


def validate(p):
    p = np.asarray(p, float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or (p <= 0).any() or not np.allclose(p.sum(1), 1):
        raise ValueError('Invalid HDA probabilities')
    return p


def elo_features(rows, k):
    """Result availability is date+7 days; no parameter/outcome access to future."""
    ordered = sorted(rows, key=lambda r: (r['date'], r['match_id']))
    ratings, features, cursor = defaultdict(float), {}, 0
    for r in ordered:
        cutoff = (date.fromisoformat(r['date']) - timedelta(days=7)).isoformat()
        while cursor < len(ordered) and ordered[cursor]['date'] <= cutoff:
            batch_day, delta = ordered[cursor]['date'], defaultdict(float)
            while cursor < len(ordered) and ordered[cursor]['date'] == batch_day:
                old = ordered[cursor]
                h, a = (old['league'], old['home']), (old['league'], old['away'])
                expected = 1 / (1 + 10 ** (np.clip(ratings[h]-ratings[a]+50, -2000, 2000)/-400))
                shift = k * ((1, .5, 0)[old['y']] - expected)
                delta[h] += shift
                delta[a] -= shift
                cursor += 1
            for team, change in delta.items():
                ratings[team] += change
        difference = (ratings[(r['league'], r['home'])] - ratings[(r['league'], r['away'])]) / 400
        features[r['match_id']] = [1., difference, abs(difference)]
    return features


def optimize(fun, x, bounds=None):
    fit = minimize(fun, x, jac=True, bounds=bounds, method='L-BFGS-B',
                   options={'maxiter': 500, 'ftol': 1e-10})
    if not fit.success or not np.isfinite(fit.fun):
        raise RuntimeError(f'Optimizer failed: {fit.message}')
    return fit.x, {'iterations': int(fit.nit), 'objective': float(fit.fun),
                   'gradient_max': float(np.max(np.abs(fit.jac)))}


def fit_logistic(train, features, ridge):
    x = np.asarray([features[r['match_id']] for r in train])
    y = np.asarray([r['y'] for r in train])
    def objective(flat):
        beta = flat.reshape(3, 3)
        scores = x @ beta
        logs = scores - logsumexp(scores, axis=1)[:, None]
        residual = np.exp(logs)
        residual[np.arange(len(y)), y] -= 1
        penalized = beta.copy(); penalized[0] = 0
        loss = -logs[np.arange(len(y)), y].sum() + ridge * (penalized**2).sum()/2
        gradient = x.T @ residual + ridge*penalized
        return loss, gradient.ravel()
    beta, audit = optimize(objective, np.zeros(9))
    return beta.reshape(3, 3), audit


def predict_logistic(model, rows, features):
    scores = np.asarray([features[r['match_id']] for r in rows]) @ model
    return validate(np.exp(scores-logsumexp(scores, axis=1)[:, None]))


def fit_poisson(train, ridge):
    teams = sorted({r[t] for r in train for t in ('home', 'away')})
    lookup = {team: i for i, team in enumerate(teams)}
    n = len(teams)
    h, a = [np.array([lookup[r[t]] for r in train]) for t in ('home', 'away')]
    hg, ag = [np.array([r[t] for r in train]) for t in ('hg', 'ag')]
    def objective(theta):
        attack = theta[2:2+n] - theta[2:2+n].mean()
        defense = theta[2+n:] - theta[2+n:].mean()
        lh = theta[0]+theta[1]+attack[h]-defense[a]
        la = theta[0]+attack[a]-defense[h]
        mh, ma = np.exp(lh), np.exp(la)
        eh, ea = mh-hg, ma-ag
        loss = (mh-hg*lh+gammaln(hg+1)+ma-ag*la+gammaln(ag+1)).sum()
        loss += ridge*((attack**2).sum()+(defense**2).sum())/2
        ga = np.bincount(h, eh, minlength=n)+np.bincount(a, ea, minlength=n)+ridge*attack
        gd = -np.bincount(a, eh, minlength=n)-np.bincount(h, ea, minlength=n)+ridge*defense
        gradient = np.r_[(eh+ea).sum(), eh.sum(), ga-ga.mean(), gd-gd.mean()]
        return loss, gradient
    initial = np.zeros(2+2*n)
    initial[0] = np.log(max(ag.mean(), .1))
    initial[1] = np.log(max(hg.mean(), .1))-initial[0]
    theta, audit = optimize(objective, initial, [(-3, 3)]*2+[(-2, 2)]*(2*n))
    return {'teams': lookup, 'theta': theta, 'n': n}, audit


def poisson_means(model, rows):
    n, theta, lookup = model['n'], model['theta'], model['teams']
    attack, defense = theta[2:2+n], theta[2+n:]
    attack, defense = attack-attack.mean(), defense-defense.mean()
    def effect(team, vector):
        return vector[lookup[team]] if team in lookup else 0.
    means = np.asarray([[np.exp(theta[0]+theta[1]+effect(r['home'], attack)-effect(r['away'], defense)),
                         np.exp(theta[0]+effect(r['away'], attack)-effect(r['home'], defense))] for r in rows])
    return np.clip(means, .02, 12), int(np.sum((means < .02) | (means > 12)))


def predict_poisson(model, rows):
    means, clipped = poisson_means(model, rows)
    scores = np.arange(41)
    ph = poisson.pmf(scores[None, :], means[:, 0, None])
    pa = poisson.pmf(scores[None, :], means[:, 1, None])
    matrix = ph[:, :, None]*pa[:, None, :]
    p = np.column_stack([np.tril(matrix, -1).sum((1, 2)),
                         np.diagonal(matrix, axis1=1, axis2=2).sum(1),
                         np.triu(matrix, 1).sum((1, 2))])
    return validate(p/p.sum(1)[:, None]), clipped


def fit_predict(family, config, train, test, features):
    p, audits = np.zeros((len(test), 3)), []
    for league in LEAGUES:
        training = [r for r in train if r['league'] == league]
        indexes = [i for i, r in enumerate(test) if r['league'] == league]
        testing = [test[i] for i in indexes]
        if family == 'elo':
            model, audit = fit_logistic(training, features[config['k']], config['ridge'])
            prediction = predict_logistic(model, testing, features[config['k']])
        else:
            model, audit = fit_poisson(training, config['ridge'])
            prediction, clipped = predict_poisson(model, testing)
            audit['clipped_prediction_means'] = clipped
            audit['teams'] = len(model['teams'])
        p[indexes] = prediction
        audits.append(dict(league=league, train_matches=len(training), **audit))
    return validate(p), audits


def match_metrics(rows, p):
    y = np.array([r['y'] for r in rows])
    return {'matches': len(rows), 'logloss': float(-np.log(p[np.arange(len(y)), y]).mean()),
            'brier': float(((p-np.eye(3)[y])**2).sum(1).mean()),
            'accuracy': float((p.argmax(1) == y).mean())}


def choose_tickets(rows, p, policy):
    """Only identities, dates and prices are read; outcomes settle later."""
    validate(p)
    if len(rows) != len(p) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate match or length mismatch')
    if policy not in POLICIES:
        raise ValueError('Unknown policy')
    grouped = defaultdict(list)
    for r, probs in zip(rows, p):
        options = []
        for outcome, (probability, odds) in enumerate(zip(probs, r['prices'])):
            ev = probability*odds-1
            eligible = (policy == 'highestprob' or
                        policy == 'p60range' and probability >= .6 and 1.5 <= odds < 2.2 or
                        policy == 'p60low' and probability >= .6 and odds < 2.2 or
                        policy == 'ev02' and ev >= .02 or policy == 'ev05' and ev >= .05)
            if eligible:
                options.append(dict(match_id=r['match_id'], outcome=outcome,
                                    probability=float(probability), odds=float(odds)))
        grouped[r['league'], r['date']].append(options)
    tickets = {}
    for group, matches in sorted(grouped.items()):
        candidates = []
        for left, right in combinations(matches, 2):
            for a, b in product(left, right):
                legs = sorted([a, b], key=lambda leg: (leg['match_id'], leg['outcome']))
                rank = np.prod([leg['probability']*(leg['odds'] if policy.startswith('ev') else 1) for leg in legs])
                candidates.append((-rank, tuple((leg['match_id'], leg['outcome']) for leg in legs), legs))
        tickets[group] = min(candidates, key=lambda item: item[:2])[2] if candidates else []
    return tickets


def settle(rows, tickets):
    actual = {r['match_id']: r for r in rows}
    days = sorted({(r['league'], r['date']) for r in rows})
    ledger = []
    for league, day in days:
        legs = tickets.get((league, day), [])
        if legs and (len(legs) != 2 or len({leg['match_id'] for leg in legs}) != 2):
            raise ValueError('Must use two distinct games')
        if any((actual[leg['match_id']]['league'], actual[leg['match_id']]['date']) != (league, day) for leg in legs):
            raise ValueError('Cross-budget ticket')
        won = int(bool(legs) and all(actual[leg['match_id']]['y'] == leg['outcome'] for leg in legs))
        stake = int(bool(legs))
        profit = float(won*np.prod([leg['odds'] for leg in legs])-stake) if legs else 0.
        ledger.append(dict(league=league, date=day, budget=1, stake=stake, won=won,
                           profit=profit, legs=legs))
    return ledger


def betting_metrics(ledger):
    days, stake = len(ledger), sum(r['stake'] for r in ledger)
    profit, wins = sum(r['profit'] for r in ledger), sum(r['won'] for r in ledger)
    return dict(days=days, tickets=stake, wins=wins, profit=profit,
                coverage=stake/days if days else 0., roi=profit/stake if stake else None,
                hit=wins/stake if stake else None, budget_return=profit/days if days else 0.)


def week(day):
    year, number, _ = date.fromisoformat(day).isocalendar()
    return f'{year}-{number:02}'


def paired_ci(rows, model, baseline, reps=REPS):
    """Weekly sums of denominator/stake/wins/profit OR count/loss; paired draws."""
    keys = sorted({week(r['date']) for r in rows})
    index = {key: i for i, key in enumerate(keys)}
    left = np.zeros((len(keys), model.shape[1]))
    right = np.zeros_like(left)
    for i, r in enumerate(rows):
        left[index[week(r['date'])]] += model[i]
        right[index[week(r['date'])]] += baseline[i]
    rng = np.random.default_rng(SEED)
    counts = rng.multinomial(len(keys), np.full(len(keys), 1/len(keys)), size=reps)
    return counts @ left, counts @ right


def interval(values):
    values = np.asarray(values)
    finite = values[np.isfinite(values)]
    return {'low': float(np.quantile(finite, .025)) if len(finite) else None,
            'high': float(np.quantile(finite, .975)) if len(finite) else None,
            'valid_replicates': len(finite)}


def betting_ci(ledger, baseline):
    if [(r['league'], r['date']) for r in ledger] != [(r['league'], r['date']) for r in baseline]:
        raise ValueError('Unpaired ledgers')
    def matrix(records):
        return np.array([[1, r['stake'], r['won'], r['profit']] for r in records])
    a, b = paired_ci(ledger, matrix(ledger), matrix(baseline))
    result = {}
    with np.errstate(divide='ignore', invalid='ignore'):
        for name, numerator, denominator in [('coverage', 1, 0), ('hit', 2, 1), ('roi', 3, 1), ('budget_return', 3, 0)]:
            x, y = a[:, numerator]/a[:, denominator], b[:, numerator]/b[:, denominator]
            result[name] = {'model': interval(x), 'minus_shin': interval(x-y)}
    return result


def loss_ci(rows, p, baseline):
    y = np.array([r['y'] for r in rows])
    a = np.column_stack([np.ones(len(rows)), -np.log(p[np.arange(len(y)), y])])
    b = np.column_stack([np.ones(len(rows)), -np.log(baseline[np.arange(len(y)), y])])
    x, z = paired_ci(rows, a, b)
    return {'model': interval(x[:, 1]/x[:, 0]), 'minus_shin': interval(x[:, 1]/x[:, 0]-z[:, 1]/z[:, 0])}


def run(rows, sources):
    start = time.perf_counter()
    protocol_commit = verify_protocol()
    if any(r['league'] not in LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected fold membership')
    features = {k: elo_features(rows, k) for k in (20, 40)}
    configurations = [('elo', {'k': k, 'ridge': ridge}) for k in (20, 40) for ridge in (1, 10)]
    configurations += [('poisson', {'ridge': ridge}) for ridge in (1, 10)]
    folds, all_trials, fit_count = [], [], 0
    for inner_year, outer_year in ((22, 23), (23, 24)):
        inner = [r for r in rows if r['season'] == inner_year and r['prices'] is not None]
        outer_all = [r for r in rows if r['season'] == outer_year]
        outer = [r for r in outer_all if r['prices'] is not None]
        if not inner or not outer or max(r['date'] for r in inner) >= min(r['date'] for r in outer_all):
            raise ValueError('Inner/outer temporal overlap or empty evaluation')
        train = guarded_training(rows, inner_year-1, [r for r in rows if r['season'] == inner_year])
        market_inner, market_outer = [validate(np.array([shin(r['prices']) for r in part])) for part in (inner, outer)]
        trials = []
        for family, config in configurations:
            prediction, audit = fit_predict(family, config, train, inner, features)
            fit_count += len(LEAGUES)
            for weight in (0., .25, .5, .75, 1.):
                candidate = f"{family}:k{config.get('k', 0)}:r{config['ridge']}:w{weight}"
                score = match_metrics(inner, (1-weight)*prediction+weight*market_inner)
                trials.append(dict(id=candidate, family=family, config=config, market_weight=weight,
                                   inner_year=2000+inner_year, metrics=score, fit_audit=audit))
        all_trials.extend(trials)
        selected = {family: min([t for t in trials if t['family'] == family],
                                key=lambda t: (t['metrics']['logloss'], t['id'])) for family in ('elo', 'poisson')}
        winner = min(trials, key=lambda t: (t['metrics']['logloss'], t['id']))
        refit = guarded_training(rows, inner_year, outer_all)
        forecasts = {'shin': market_outer}
        outer_audits = {}
        for family, chosen in selected.items():
            p, audit = fit_predict(family, chosen['config'], refit, outer, features)
            fit_count += len(LEAGUES)
            forecasts[family] = validate((1-chosen['market_weight'])*p + chosen['market_weight']*market_outer)
            outer_audits[family] = audit
        forecasts['inner_selected'] = forecasts[winner['family']]
        baseline_ledgers = {policy: settle(outer_all, choose_tickets(outer, market_outer, policy)) for policy in POLICIES}
        reports = {}
        for name, prediction in forecasts.items():
            policies = {}
            for policy in POLICIES:
                ledger = baseline_ledgers[policy] if name == 'shin' else settle(outer_all, choose_tickets(outer, prediction, policy))
                policies[policy] = dict(metrics=betting_metrics(ledger), ci=betting_ci(ledger, baseline_ledgers[policy]), ledger=ledger)
            reports[name] = dict(metrics=match_metrics(outer, prediction),
                                 logloss_ci=loss_ci(outer, prediction, market_outer), policies=policies)
        folds.append(dict(inner_year=2000+inner_year, outer_year=2000+outer_year,
                          training_matches=len(train), refit_matches=len(refit), inner_matches=len(inner), outer_matches=len(outer),
                          outer_actual_matches=len(outer_all), outer_missing_prices=len(outer_all)-len(outer),
                          selected_ids={f: t['id'] for f, t in selected.items()}, pooled_selected_id=winner['id'],
                          fit_audits=outer_audits, reports=reports,
                          forecasts=[dict(**r, probabilities={name: p[i].tolist() for name, p in forecasts.items()}) for i, r in enumerate(outer)]))
    return dict(status='exploratory retrospective; NOT new holdout or actual Proto ROI',
                protocol_commit=protocol_commit, protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
                code_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in (Path(__file__).resolve(), ROOT/'src/devig.py')},
                blas_threads=1,
                seed=SEED, bootstrap_replicates=REPS, trial_count=len(all_trials),
                actual_league_fits=fit_count, optimizer_failures=[], sources=sources,
                versions=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                runtime_seconds=time.perf_counter()-start, trials=all_trials, folds=folds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/research/2026-10-01-score-tournament-results.json')
    args = parser.parse_args()
    verify_protocol()  # Mandatory before any dataset access.
    rows, sources = load_sources()
    result = run(rows, sources)
    # This is a generated research artifact, not source editing.
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('trial_count', 'actual_league_fits', 'runtime_seconds', 'protocol_commit')}))
    for fold in result['folds']:
        print(fold['outer_year'], fold['selected_ids'], fold['pooled_selected_id'])
        print(json.dumps({name: r['metrics'] for name, r in fold['reports'].items()}))


if __name__ == '__main__':
    main()
