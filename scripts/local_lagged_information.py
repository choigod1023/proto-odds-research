"""Frozen lagged information ablation; research only, no production mutations."""
from __future__ import annotations
import argparse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

for _key in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import local_score_model_tournament as base

PROTOCOL = ROOT/'docs/research/2026-10-01-lagged-information-protocol.md'
PROTOCOL_COMMIT = 'eaaf6f43'
FEATURES = ('home_net_shots', 'away_net_shots', 'home_net_sot', 'away_net_sot',
            'home_rest', 'away_rest', 'home_count7', 'away_count7', 'home_count14',
            'away_count14', 'home_surprise', 'away_surprise')
FAMILIES = {'bias_only': [], 'shots': list(range(4)), 'schedule': list(range(4, 10)),
            'surprise': [10, 11], 'all': list(range(12)), 'no_shots': list(range(4, 12)),
            'no_schedule': [0, 1, 2, 3, 10, 11], 'no_surprise': list(range(10))}
STAT_KEYS = ('HS', 'AS', 'HST', 'AST')


def lf(raw):
    return raw.replace(b'\r\n', b'\n')


def verify_protocol():
    committed = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(ROOT).as_posix()}'], cwd=ROOT)
    if lf(committed) != lf(PROTOCOL.read_bytes()):
        raise ValueError('Frozen lagged-information protocol changed')
    return subprocess.check_output(['git', 'rev-parse', PROTOCOL_COMMIT], cwd=ROOT, text=True).strip()


def parse_statistics(raw, league, year):
    rows = base.parse_source(raw, league, year)
    by_id = {r['match_id']: r for r in rows}
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not set(STAT_KEYS).issubset(reader.fieldnames):
        raise ValueError('Missing shot columns')
    for record in reader:
        if not record.get('HomeTeam'):
            continue
        day = datetime.strptime(record['Date'], '%d/%m/%Y' if len(record['Date'].split('/')[-1]) == 4 else '%d/%m/%y').date()
        identity = f"{league}:{day}:{record['HomeTeam']}:{record['AwayTeam']}"
        stats = {}
        for key in STAT_KEYS:
            try:
                value = float(record[key])
                stats[key] = value if np.isfinite(value) and value >= 0 else None
            except (TypeError, ValueError):
                stats[key] = None
        by_id[identity]['stats'] = stats
    return rows


def load_sources():
    def one(item):
        league, directory, year = item
        url = f'https://raw.githubusercontent.com/sosthene14/footballdataset/{base.REV}/datasets/{directory}/{year:02}{year+1:02}_{league}.csv'
        raw = urllib.request.urlopen(url, timeout=30).read()
        rows = parse_statistics(raw, league, year)
        return rows, dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), matches=len(rows),
                          missing_prices=sum(r['prices'] is None for r in rows),
                          missing_stats={key: sum(r['stats'][key] is None for r in rows) for key in STAT_KEYS})
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(one, [(lg, directory, year) for lg, directory in base.LEAGUES.items() for year in range(19, 25)]))
    return sorted([r for rows, _ in parts for r in rows], key=lambda r: (r['date'], r['match_id'])), [source for _, source in parts]


def feature_rows(rows):
    """Two clocks: past schedule dates, and results/statistics available at +7d."""
    ordered = sorted(rows, key=lambda r: (r['date'], r['match_id']))
    if len({r['match_id'] for r in ordered}) != len(ordered):
        raise ValueError('Duplicate match identity')
    history = defaultdict(lambda: deque(maxlen=8))
    schedules = defaultdict(list)
    cursor, schedule_cursor, result = 0, 0, {}
    def mean(values):
        finite = [v for v in values if np.isfinite(v)]
        return float(np.mean(finite)) if finite else np.nan
    for row in ordered:
        today = date.fromisoformat(row['date'])
        cutoff = (today-timedelta(days=7)).isoformat()
        while cursor < len(ordered) and ordered[cursor]['date'] <= cutoff:
            old = ordered[cursor]
            stats = old['stats']
            net = stats['HS']-stats['AS'] if stats['HS'] is not None and stats['AS'] is not None else np.nan
            sot = stats['HST']-stats['AST'] if stats['HST'] is not None and stats['AST'] is not None else np.nan
            surprise = np.nan
            if old['prices'] is not None:
                p = base.shin(old['prices'])
                surprise = (1., .5, 0.)[old['y']] - (p[0]+.5*p[1])
            history[old['league'], old['home']].append((net, sot, surprise))
            history[old['league'], old['away']].append((-net, -sot, -surprise))
            cursor += 1
        while schedule_cursor < len(ordered) and ordered[schedule_cursor]['date'] < row['date']:
            old = ordered[schedule_cursor]
            past_day = date.fromisoformat(old['date'])
            for side in ('home', 'away'):
                schedules[old['league'], old[side]].append(past_day)
            schedule_cursor += 1
        sides = []
        for side in ('home', 'away'):
            key = row['league'], row[side]
            recent = list(history[key])
            days = schedules[key]
            rest = min(30, (today-days[-1]).days) if days else np.nan
            count7 = sum(0 < (today-day).days <= 7 for day in days)
            count14 = sum(0 < (today-day).days <= 14 for day in days)
            sides.append([mean([r[0] for r in recent]), mean([r[1] for r in recent]),
                          rest, count7, count14, mean([r[2] for r in recent])])
        h, a = sides
        result[row['match_id']] = np.array([h[0], a[0], h[1], a[1], h[2], a[2],
                                           h[3], a[3], h[4], a[4], h[5], a[5]], float)
    return result


def fit_scaler(raw):
    medians = np.array([np.median(col[np.isfinite(col)]) if np.isfinite(col).any() else 0. for col in raw.T])
    imputed = np.where(np.isfinite(raw), raw, medians)
    means, scales = imputed.mean(0), imputed.std(0)
    return {'median': medians, 'mean': means, 'scale': np.where(scales > 0, scales, 1.)}


def transform(raw, scaler):
    missing = ~np.isfinite(raw)
    values = np.where(missing, scaler['median'], raw)
    return np.column_stack([np.ones(len(raw)), (values-scaler['mean'])/scaler['scale'], missing.astype(float)])


def design(rows, features, family):
    return np.asarray([features[r['match_id']][FAMILIES[family]] for r in rows]).reshape(len(rows), len(FAMILIES[family]))


def objective(beta, x, market, y, ridge):
    coefficients = beta.reshape(x.shape[1], 3)
    scores = np.log(market)+x@coefficients
    logs = scores-logsumexp(scores, axis=1)[:, None]
    errors = np.exp(logs)
    errors[np.arange(len(y)), y] -= 1
    value = -logs[np.arange(len(y)), y].mean()+ridge/2*np.sum(coefficients**2)
    gradient = x.T@errors/len(y)+ridge*coefficients
    return float(value), gradient.ravel()


def fit_model(rows, features, family, ridge):
    raw = design(rows, features, family)
    scaler = fit_scaler(raw)
    x = transform(raw, scaler)
    market = base.validate(np.array([base.shin(r['prices']) for r in rows]))
    y = np.array([r['y'] for r in rows])
    fit = minimize(objective, np.zeros(x.shape[1]*3), args=(x, market, y, ridge), jac=True,
                   method='L-BFGS-B', options={'maxiter': 500, 'ftol': 1e-10})
    if not fit.success or not np.isfinite(fit.fun):
        raise RuntimeError(f'Optimizer failed: {fit.message}')
    model = dict(beta=fit.x.reshape(x.shape[1], 3), scaler=scaler, family=family)
    audit = dict(matches=len(rows), iterations=int(fit.nit), objective=float(fit.fun),
                 gradient_max=float(np.max(np.abs(fit.jac))),
                 train_missing=np.sum(~np.isfinite(raw), axis=0).tolist(),
                 features=[FEATURES[i] for i in FAMILIES[family]],
                 scaler={key: value.tolist() for key, value in scaler.items()}, coefficients=model['beta'].tolist())
    return model, audit


def predict(model, rows, features):
    x = transform(design(rows, features, model['family']), model['scaler'])
    market = base.validate(np.array([base.shin(r['prices']) for r in rows]))
    scores = np.log(market)+x@model['beta']
    return base.validate(np.exp(scores-logsumexp(scores, axis=1)[:, None]))


def fit_predict(train, evaluation, features, family, ridge):
    p, audits = np.zeros((len(evaluation), 3)), []
    for league in base.LEAGUES:
        training = [r for r in train if r['league'] == league and r['prices'] is not None]
        indices = [i for i, r in enumerate(evaluation) if r['league'] == league]
        model, audit = fit_model(training, features, family, ridge)
        p[indices] = predict(model, [evaluation[i] for i in indices], features)
        audits.append(dict(league=league, **audit))
    return base.validate(p), audits


def run(rows, sources):
    protocol_commit = verify_protocol()
    if any(r['league'] not in base.LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected fold membership')
    started = time.perf_counter()
    features = feature_rows(rows)
    folds, trials, fits = [], [], 0
    for inner_year, outer_year in ((22, 23), (23, 24)):
        inner_all = [r for r in rows if r['season'] == inner_year]
        outer_all = [r for r in rows if r['season'] == outer_year]
        inner = [r for r in inner_all if r['prices'] is not None]
        outer = [r for r in outer_all if r['prices'] is not None]
        train = base.guarded_training(rows, inner_year-1, inner_all)
        refit = base.guarded_training(rows, inner_year, outer_all)
        if max(r['date'] for r in inner_all) >= min(r['date'] for r in outer_all):
            raise ValueError('Inner/outer overlap')
        market_inner, market_outer = [base.validate(np.array([base.shin(r['prices']) for r in part])) for part in (inner, outer)]
        current = [dict(id='shin', family='shin', ridge=None, inner_year=2000+inner_year,
                        metrics=base.match_metrics(inner, market_inner), fit_audit=[])]
        for family in FAMILIES:
            for ridge in (.1, 1., 10.):
                p, audit = fit_predict(train, inner, features, family, ridge)
                fits += 2
                current.append(dict(id=f'{family}:r{ridge}', family=family, ridge=ridge,
                                    inner_year=2000+inner_year, metrics=base.match_metrics(inner, p), fit_audit=audit))
        trials.extend(current)
        selected = {family: min([t for t in current if t['family'] == family],
                                key=lambda t: (t['metrics']['logloss'], t['id'])) for family in FAMILIES}
        pooled = min(current, key=lambda t: (t['metrics']['logloss'], t['id']))
        forecasts, audits = {'shin': market_outer}, {}
        for family, trial in selected.items():
            forecasts[family], audits[family] = fit_predict(refit, outer, features, family, trial['ridge'])
            fits += 2
        forecasts['inner_selected'] = forecasts[pooled['family']]
        ledgers = {name: {policy: base.settle(outer_all, base.choose_tickets(outer, p, policy)) for policy in base.POLICIES}
                   for name, p in forecasts.items()}
        reports = {}
        for name, p in forecasts.items():
            loss_ci = {ref: base.loss_ci(outer, p, forecasts[ref]) for ref in ('shin', 'bias_only')}
            for item in loss_ci.values():
                item['difference'] = item.pop('minus_shin')
            policies = {}
            for policy in base.POLICIES:
                ci = {ref: base.betting_ci(ledgers[name][policy], ledgers[ref][policy]) for ref in ('shin', 'bias_only')}
                for comparison in ci.values():
                    for item in comparison.values():
                        item['difference'] = item.pop('minus_shin')
                policies[policy] = dict(metrics=base.betting_metrics(ledgers[name][policy]), ci=ci, ledger=ledgers[name][policy])
            reports[name] = dict(metrics=base.match_metrics(outer, p), loss_ci=loss_ci, policies=policies)
        folds.append(dict(inner_year=2000+inner_year, outer_year=2000+outer_year,
                          train_matches=len(train), refit_matches=len(refit), outer_matches=len(outer),
                          selected_ids={f: t['id'] for f, t in selected.items()}, pooled_selected_id=pooled['id'],
                          fit_audits=audits, reports=reports,
                          forecasts=[dict(match_id=r['match_id'], league=r['league'], date=r['date'], y=r['y'], prices=r['prices'],
                                          lagged_features=[float(x) if np.isfinite(x) else None for x in features[r['match_id']]],
                                          probabilities={name: p[i].tolist() for name, p in forecasts.items()}) for i, r in enumerate(outer)]))
    paths = [Path(__file__).resolve(), ROOT/'scripts/local_score_model_tournament.py', ROOT/'src/devig.py']
    return dict(status='exploratory exposed-data lagged information ablation; NOT new holdout or actual Proto ROI',
                protocol_commit=protocol_commit, protocol_sha256_lf=hashlib.sha256(lf(PROTOCOL.read_bytes())).hexdigest(),
                code_sha256_lf={p.relative_to(ROOT).as_posix(): hashlib.sha256(lf(p.read_bytes())).hexdigest() for p in paths},
                feature_names=list(FEATURES), source=sources, trial_count=len(trials), actual_league_fits=fits,
                seed=base.SEED, bootstrap_replicates=base.REPS, optimizer_failures=[],
                versions=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                runtime_seconds=time.perf_counter()-started, trials=trials, folds=folds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/research/2026-10-01-lagged-information-results.json')
    args = parser.parse_args()
    verify_protocol()
    rows, sources = load_sources()
    result = run(rows, sources)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('trial_count', 'actual_league_fits', 'runtime_seconds')}))
    for fold in result['folds']:
        print(fold['outer_year'], fold['pooled_selected_id'])
        print(json.dumps({name: r['metrics']['logloss'] for name, r in fold['reports'].items()}))


if __name__ == '__main__':
    main()
