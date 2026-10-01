"""Frozen, local GAP-style follow-up. No production use or new holdout claim."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

for _key in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import local_lagged_information as info
np, base = info.np, info.base
PROTOCOL = ROOT/'docs/research/2026-10-01-gap-information-protocol.md'
PROTOCOL_COMMIT = 'c1c875e9'
REFERENCE = ROOT/'docs/research/2026-10-01-lagged-information-results.json'
GRID = tuple((a, p) for a in (.05, .10, .20) for p in (.50, .80))
KEYS = ('HS', 'AS', 'HST', 'AST')


def digest(path):
    return hashlib.sha256(info.lf(path.read_bytes())).hexdigest()


def verify_protocol():
    committed = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(ROOT).as_posix()}'], cwd=ROOT)
    if info.lf(committed) != info.lf(PROTOCOL.read_bytes()):
        raise ValueError('Frozen GAP protocol changed')
    return subprocess.check_output(['git', 'rev-parse', PROTOCOL_COMMIT], cwd=ROOT, text=True).strip()


def update(home, away, sh, sa, alpha, phi):
    """Appendix (7),(8), symmetric phi1=phi2; arrays [Ha,Hd,Aa,Ad]."""
    eh = sh-(home[0]+away[3])/2
    ea = sa-(away[2]+home[1])/2
    hdelta = np.array([phi*eh, phi*ea, (1-phi)*eh, (1-phi)*ea])
    adelta = np.array([(1-phi)*ea, (1-phi)*eh, phi*ea, phi*eh])
    return np.maximum(home+alpha*hdelta, 0), np.maximum(away+alpha*adelta, 0)


def feature_rows(rows, alpha, phi):
    ordered = sorted(rows, key=lambda r: (r['date'], r['match_id']))
    if len({r['match_id'] for r in ordered}) != len(ordered):
        raise ValueError('Duplicate match identity')
    states = defaultdict(lambda: np.zeros((2, 4)))
    cursor, features = 0, {}
    for row in ordered:
        cutoff = (date.fromisoformat(row['date'])-timedelta(days=7)).isoformat()
        while cursor < len(ordered) and ordered[cursor]['date'] <= cutoff:
            old = ordered[cursor]
            hk, ak = (old['league'], old['home']), (old['league'], old['away'])
            for j, (hkey, akey) in enumerate((('HS', 'AS'), ('HST', 'AST'))):
                sh, sa = old['stats'][hkey], old['stats'][akey]
                if sh is not None and sa is not None:
                    states[hk][j], states[ak][j] = update(states[hk][j], states[ak][j], sh, sa, alpha, phi)
            cursor += 1
        h, a = states[(row['league'], row['home'])], states[(row['league'], row['away'])]
        features[row['match_id']] = np.array([(h[0, 0]+a[0, 3])/2, (a[0, 2]+h[0, 1])/2,
                                             (h[1, 0]+a[1, 3])/2, (a[1, 2]+h[1, 1])/2])
    return features


def market(rows):
    return base.validate(np.array([base.shin(r['prices']) for r in rows]))


def fit_predict(train, evaluation, features):
    predictions, audits = np.zeros((len(evaluation), 3)), []
    for league in base.LEAGUES:
        training = [r for r in train if r['league'] == league and r['prices'] is not None]
        indices = [i for i, r in enumerate(evaluation) if r['league'] == league]
        raw = np.array([features[r['match_id']] for r in training])
        scaler = info.fit_scaler(raw)
        x = info.transform(raw, scaler)
        fit = info.minimize(info.objective, np.zeros(x.shape[1]*3),
                            args=(x, market(training), np.array([r['y'] for r in training]), 1.),
                            jac=True, method='L-BFGS-B', options={'maxiter': 500, 'ftol': 1e-10})
        if not fit.success or not np.isfinite(fit.fun):
            raise RuntimeError(f'Optimizer failed: {fit.message}')
        part = [evaluation[i] for i in indices]
        xe = info.transform(np.array([features[r['match_id']] for r in part]), scaler)
        scores = np.log(market(part))+xe@fit.x.reshape(x.shape[1], 3)
        predictions[indices] = np.exp(scores-info.logsumexp(scores, axis=1)[:, None])
        audits.append(dict(league=league, matches=len(training), objective=float(fit.fun),
                           iterations=int(fit.nit), gradient_max=float(np.abs(fit.jac).max()),
                           scaler={k: v.tolist() for k, v in scaler.items()}, coefficients=fit.x.tolist()))
    return base.validate(predictions), audits


def mean_statistics(train):
    result = {}
    for league in base.LEAGUES:
        result[league] = []
        for key in KEYS:
            values = [r['stats'][key] for r in train if r['league'] == league and r['stats'][key] is not None]
            if not values:
                raise ValueError(f'No training observations: {league} {key}')
            result[league].append(float(np.mean(values)))
    return result


def stat_metrics(rows, features, means):
    result = {}
    for j, key in enumerate(KEYS):
        available = [r for r in rows if r['stats'][key] is not None]
        y = np.array([r['stats'][key] for r in available])
        gap = np.array([features[r['match_id']][j] for r in available])-y
        reference = np.array([means[r['league']][j] for r in available])-y
        result[key] = dict(observations=len(y), missing=len(rows)-len(y),
                           gap=dict(mae=float(np.abs(gap).mean()), mse=float(np.mean(gap**2))),
                           train_mean=dict(mae=float(np.abs(reference).mean()), mse=float(np.mean(reference**2))))
    return result


def reference_predictions(rows, fold):
    records = {r['match_id']: r for r in fold['forecasts']}
    if len(records) != len(fold['forecasts']) or set(records) != {r['match_id'] for r in rows}:
        raise ValueError('Reference identity mismatch')
    for row in rows:
        if any(records[row['match_id']][k] != row[k] for k in ('league', 'date', 'y', 'prices')):
            raise ValueError('Reference source values changed')
    return base.validate(np.array([records[r['match_id']]['probabilities']['inner_selected'] for r in rows]))


def rename_difference(value):
    if isinstance(value, dict):
        return {('difference' if k == 'minus_shin' else k): rename_difference(v) for k, v in value.items()}
    return value


def summarize(rows, forecasts, ledgers):
    reports = {}
    for name, p in forecasts.items():
        comparisons = ('shin', 'lagged_inner_selected') if name == 'gap' else ()
        reports[name] = dict(metrics=base.match_metrics(rows, p),
            loss_ci={ref: rename_difference(base.loss_ci(rows, p, forecasts[ref])) for ref in comparisons},
            policies={policy: dict(metrics=base.betting_metrics(ledgers[name][policy]),
                ci={ref: rename_difference(base.betting_ci(ledgers[name][policy], ledgers[ref][policy])) for ref in comparisons})
                for policy in base.POLICIES})
    return reports


def run(rows, sources, reference, protocol_commit):
    started = time.perf_counter()
    if sources != reference['source']:
        raise ValueError('Source provenance changed from previous experiment')
    if any(r['league'] not in base.LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected fold membership')
    features = {f'a{a}:p{p}': feature_rows(rows, a, p) for a, p in GRID}
    trials, folds, pooled_rows = [], [], []
    pooled_p = {name: [] for name in ('shin', 'lagged_inner_selected', 'gap')}
    pooled_ledgers = {name: {policy: [] for policy in base.POLICIES} for name in pooled_p}
    for inner_year, outer_year in ((22, 23), (23, 24)):
        inner_all = [r for r in rows if r['season'] == inner_year]
        outer_all = [r for r in rows if r['season'] == outer_year]
        if max(r['date'] for r in inner_all) >= min(r['date'] for r in outer_all):
            raise ValueError('Inner/outer overlap')
        train = base.guarded_training(rows, inner_year-1, inner_all)
        refit = base.guarded_training(rows, inner_year, outer_all)
        inner = [r for r in inner_all if r['prices'] is not None]
        outer = [r for r in outer_all if r['prices'] is not None]
        current = []
        for identity, feature in features.items():
            p, audit = fit_predict(train, inner, feature)
            current.append(dict(id=identity, inner_year=2000+inner_year, metrics=base.match_metrics(inner, p),
                                statistics=stat_metrics(inner_all, feature, mean_statistics(train)), fit_audit=audit))
        trials.extend(current)
        selected = min(current, key=lambda t: (t['metrics']['logloss'], t['id']))
        feature = features[selected['id']]
        gap, audit = fit_predict(refit, outer, feature)
        old_fold = next(f for f in reference['folds'] if f['outer_year'] == outer_year+2000)
        forecasts = dict(shin=market(outer), lagged_inner_selected=reference_predictions(outer, old_fold), gap=gap)
        ledgers = {name: {policy: base.settle(outer_all, base.choose_tickets(outer, p, policy)) for policy in base.POLICIES}
                   for name, p in forecasts.items()}
        reports = summarize(outer, forecasts, ledgers)
        means = mean_statistics(refit)
        folds.append(dict(outer_year=outer_year+2000, inner_year=inner_year+2000, selected_id=selected['id'],
            reference_selected_id=old_fold['pooled_selected_id'], train_matches=len(train), refit_matches=len(refit),
            train_last_date=max(r['date'] for r in train), refit_last_date=max(r['date'] for r in refit),
            inner_first_date=min(r['date'] for r in inner_all), outer_first_date=min(r['date'] for r in outer_all),
            statistics=stat_metrics(outer_all, feature, means), train_stat_means=means, fit_audit=audit,
            reports=reports, ledgers=ledgers,
            forecasts=[dict(match_id=r['match_id'], league=r['league'], date=r['date'], y=r['y'], prices=r['prices'],
                probabilities={name: p[i].tolist() for name, p in forecasts.items()}) for i, r in enumerate(outer)],
            statistic_forecasts=[dict(match_id=r['match_id'], actual=r['stats'], predicted=feature[r['match_id']].tolist(),
                                     train_mean=means[r['league']]) for r in outer_all]))
        pooled_rows.extend(outer)
        for name, p in forecasts.items():
            pooled_p[name].append(p)
            for policy in base.POLICIES:
                pooled_ledgers[name][policy].extend(ledgers[name][policy])
    paths = [Path(__file__).resolve(), ROOT/'scripts/local_lagged_information.py',
             ROOT/'scripts/local_score_model_tournament.py', ROOT/'src/devig.py']
    pooled = summarize(pooled_rows, {k: np.concatenate(v) for k, v in pooled_p.items()}, pooled_ledgers)
    return dict(status='Post-lagged exploratory follow-up on exposed data; NOT a new holdout or actual Proto return',
        protocol_commit=protocol_commit, protocol_sha256_lf=digest(PROTOCOL),
        code_sha256_lf={p.relative_to(ROOT).as_posix(): digest(p) for p in paths},
        reference_sha256_lf=digest(REFERENCE), source=sources, grid=GRID, trial_count=len(trials), actual_league_fits=28,
        seed=base.SEED, bootstrap_replicates=base.REPS, versions=dict(python=info.platform.python_version(),
        numpy=np.__version__, scipy=info.scipy.__version__), optimizer_failures=[],
        runtime_seconds=time.perf_counter()-started, trials=trials, folds=folds, pooled=pooled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/research/2026-10-01-gap-information-results.json')
    args = parser.parse_args()
    protocol_commit = verify_protocol()  # Must precede data loading.
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))
    for path, expected in reference['code_sha256_lf'].items():
        if digest(ROOT/path) != expected:
            raise ValueError(f'Previous experiment dependency changed: {path}')
    rows, sources = info.load_sources()
    result = run(rows, sources, reference, protocol_commit)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('trial_count', 'actual_league_fits', 'runtime_seconds')}))
    for fold in result['folds']:
        print(fold['outer_year'], fold['selected_id'], json.dumps(fold['statistics']))
    print(json.dumps(result['pooled']))


if __name__ == '__main__':
    main()
