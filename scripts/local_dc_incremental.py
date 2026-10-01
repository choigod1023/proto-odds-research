"""Frozen local DC/recency ablation; no production writes or old artifact edits."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

for _variable in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_variable] = '1'

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import local_score_model_tournament as base
from src.score_dist import dixon_coles_tau

PROTOCOL = ROOT/'docs/research/2026-10-01-dc-protocol.md'
PROTOCOL_COMMIT = '751451c5'
EPS = 1e-6
RIDGE = 10.
CONFIGS = [dict(family='independent', dc=False, half_life=None),
           dict(family='dc', dc=True, half_life=None)] + [
               dict(family=family, dc=dc, half_life=half_life)
               for family, dc in [('recency', False), ('dc_recency', True)] for half_life in (180, 365)]


def lf(raw):
    return raw.replace(b'\r\n', b'\n')


def verify_protocol():
    committed = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(ROOT).as_posix()}'], cwd=ROOT)
    if lf(PROTOCOL.read_bytes()) != lf(committed):
        raise ValueError('Frozen DC protocol differs from pinned commit')
    return subprocess.check_output(['git', 'rev-parse', PROTOCOL_COMMIT], cwd=ROOT, text=True).strip()


def weights(rows, half_life):
    dates = [date.fromisoformat(r['date']) for r in rows]
    anchor = max(dates)
    raw = np.ones(len(rows)) if half_life is None else np.exp2(-np.array([(anchor-d).days for d in dates])/half_life)
    return raw * (len(rows)/raw.sum())


def rho_transform(theta, n, dc):
    """Conservative feasible rho and its exact piecewise derivative.

    Centered neutral effects lie inside their extrema, so unseen teams are also
    covered without inspecting evaluation team names or future outcomes.
    """
    attack = theta[2:2+n]-theta[2:2+n].mean()
    defense = theta[2+n:2+2*n]-theta[2+n:2+2*n].mean()
    gradient = np.zeros_like(theta)
    gradient[0] = 1
    gradient[2:2+n] = -1/n
    gradient[2+int(np.argmax(attack))] += 1
    gradient[2+n:2+2*n] = 1/n
    gradient[2+n+int(np.argmin(defense))] -= 1
    ga = gradient.copy()
    gh = gradient.copy(); gh[1] = 1
    max_away = float(np.exp(theta[0]+attack.max()-defense.min()))
    max_home = float(np.exp(theta[0]+theta[1]+attack.max()-defense.min()))
    positive = min(.3, (1-EPS)/(max_home*max_away))
    negative = min(.3, (1-EPS)/max(max_home, max_away))
    if not dc:
        return 0., np.zeros_like(theta), (-negative, positive), (max_home, max_away)
    t = np.tanh(theta[-1])
    if t >= 0:
        magnitude = positive
        dm = -magnitude*(gh+ga) if magnitude < .3 else np.zeros_like(theta)
    else:
        magnitude = negative
        dm = -magnitude*(gh if max_home >= max_away else ga) if magnitude < .3 else np.zeros_like(theta)
    derivative = t*dm
    derivative[-1] += magnitude*(1-t*t)
    return float(t*magnitude), derivative, (-negative, positive), (max_home, max_away)


def make_objective(rows, half_life, dc):
    teams = {name: i for i, name in enumerate(sorted({r[t] for r in rows for t in ('home', 'away')}))}
    n = len(teams)
    h, a = [np.array([teams[r[t]] for r in rows]) for t in ('home', 'away')]
    hg, ag = [np.array([r[t] for r in rows]) for t in ('hg', 'ag')]
    w = weights(rows, half_life)
    def objective(theta):
        attack = theta[2:2+n]-theta[2:2+n].mean()
        defense = theta[2+n:2+2*n]-theta[2+n:2+2*n].mean()
        lh, la = theta[0]+theta[1]+attack[h]-defense[a], theta[0]+attack[a]-defense[h]
        mh, ma = np.exp(lh), np.exp(la)
        rho, drho, _, _ = rho_transform(theta, n, dc)
        tau, dh, da, dr = np.ones(len(rows)), np.zeros(len(rows)), np.zeros(len(rows)), np.zeros(len(rows))
        if dc:
            for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
                mask = (hg == i) & (ag == j)
                tau[mask] = dixon_coles_tau(i, j, mh[mask], ma[mask], rho)
                if (i, j) == (0, 0):
                    dh[mask] = da[mask] = -mh[mask]*ma[mask]*rho
                    dr[mask] = -mh[mask]*ma[mask]
                elif (i, j) == (0, 1):
                    dh[mask], dr[mask] = mh[mask]*rho, mh[mask]
                elif (i, j) == (1, 0):
                    da[mask], dr[mask] = ma[mask]*rho, ma[mask]
                else:
                    dr[mask] = -1
        if (tau <= 0).any():
            raise ValueError('Infeasible tau in objective')
        eh, ea = w*(mh-hg-dh/tau), w*(ma-ag-da/tau)
        loss = np.sum(w*(mh-hg*lh+gammaln(hg+1)+ma-ag*la+gammaln(ag+1)-np.log(tau)))
        loss += RIDGE/2*((attack**2).sum()+(defense**2).sum())
        ga = np.bincount(h, eh, minlength=n)+np.bincount(a, ea, minlength=n)+RIDGE*attack
        gd = -np.bincount(a, eh, minlength=n)-np.bincount(h, ea, minlength=n)+RIDGE*defense
        gradient = np.r_[(eh+ea).sum(), eh.sum(), ga-ga.mean(), gd-gd.mean()]
        if dc:
            gradient = np.r_[gradient, 0.] - np.sum(w*dr/tau)*drho
        return float(loss), gradient
    return objective, teams, w


def fit_model(rows, config):
    objective, teams, w = make_objective(rows, config['half_life'], config['dc'])
    n = len(teams)
    theta = np.zeros(2+2*n+int(config['dc']))
    theta[0] = np.log(max(np.average([r['ag'] for r in rows], weights=w), .1))
    theta[1] = np.log(max(np.average([r['hg'] for r in rows], weights=w), .1))-theta[0]
    bounds = [(-3, 3)]*2+[(-2, 2)]*(2*n)+([(-4, 4)] if config['dc'] else [])
    result = minimize(objective, theta, jac=True, method='L-BFGS-B', bounds=bounds,
                      options={'maxiter': 500, 'ftol': 1e-10})
    if not result.success or not np.isfinite(result.fun):
        raise RuntimeError(f'Optimizer failure: {result.message}')
    rho, _, feasible, maxima = rho_transform(result.x, n, config['dc'])
    audit = dict(matches=len(rows), teams=n, rho=rho, feasible_rho=list(feasible),
                 rate_maxima=list(maxima), iterations=int(result.nit), objective=float(result.fun),
                 gradient_max=float(np.max(np.abs(result.jac))), weight_sum=float(w.sum()),
                 effective_n=float(w.sum()**2/(w*w).sum()),
                 min_tau_guarantee=float(min(1-maxima[0]*maxima[1]*max(rho, 0),
                                             1+max(maxima)*min(rho, 0), 1-rho)),
                 last_train_date=max(r['date'] for r in rows))
    return dict(theta=result.x, teams=teams, n=n, dc=config['dc']), audit


def prediction_rates(model, rows):
    theta, n, lookup = model['theta'], model['n'], model['teams']
    attack = theta[2:2+n]-theta[2:2+n].mean()
    defense = theta[2+n:2+2*n]-theta[2+n:2+2*n].mean()
    def effect(name, values):
        return values[lookup[name]] if name in lookup else 0.
    return np.array([[np.exp(theta[0]+theta[1]+effect(r['home'], attack)-effect(r['away'], defense)),
                      np.exp(theta[0]+effect(r['away'], attack)-effect(r['home'], defense))] for r in rows])


def predict(model, rows):
    means = prediction_rates(model, rows)
    rho, _, _, maxima = rho_transform(model['theta'], model['n'], model['dc'])
    support = max(40, int(poisson.ppf(1-1e-13, means.max())))
    if support > 2000:
        raise ValueError('Unexpectedly large fitted rate; refuse unbounded grid')
    support_values = np.arange(support+1)
    ph = poisson.pmf(support_values[None, :], means[:, 0, None])
    pa = poisson.pmf(support_values[None, :], means[:, 1, None])
    p = np.column_stack([
        np.sum(ph*poisson.cdf(support_values[None, :]-1, means[:, 1, None]), axis=1),
        np.sum(ph*pa, axis=1),
        np.sum(pa*poisson.cdf(support_values[None, :]-1, means[:, 0, None]), axis=1)])
    mass = ph.sum(1)*pa.sum(1)
    min_tau = 1.
    for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
        tau = np.asarray(dixon_coles_tau(i, j, means[:, 0], means[:, 1], rho))
        if np.any(tau <= 0):
            raise ValueError('Prediction tau not positive')
        min_tau = min(min_tau, float(tau.min()))
        delta = ph[:, i]*pa[:, j]*(tau-1)
        p[:, 0 if i > j else 1 if i == j else 2] += delta
        mass += delta
    if not np.allclose(mass, 1., atol=3e-12, rtol=0):
        raise ValueError('Score mass not normalized')
    if not np.allclose(p.sum(1), mass, atol=3e-12, rtol=0):
        raise ValueError('HDA mass inconsistent with joint distribution')
    return base.validate(p/mass[:, None]), dict(score_support=support, min_tau=min_tau,
                                             maximum_mass_error=float(np.max(abs(mass-1))))


def fit_predict(train, evaluation, config):
    prediction = np.zeros((len(evaluation), 3))
    audit = []
    for league in base.LEAGUES:
        selected = [r for r in train if r['league'] == league]
        indices = [i for i, r in enumerate(evaluation) if r['league'] == league]
        model, info = fit_model(selected, config)
        probabilities, prediction_info = predict(model, [evaluation[i] for i in indices])
        prediction[indices] = probabilities
        audit.append(dict(league=league, **info, **prediction_info))
    return base.validate(prediction), audit


def config_id(config):
    return f"{config['family']}:half{config['half_life'] or 0}"


def run(rows, sources):
    protocol_commit = verify_protocol()
    if any(r['league'] not in base.LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected fold membership')
    started = time.perf_counter()
    folds, trials, league_fits = [], [], 0
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
        current = []
        for config in CONFIGS:
            p, audit = fit_predict(train, inner, config)
            league_fits += 2
            for weight in (0., .25, .5, .75, 1.):
                current.append(dict(id=f'{config_id(config)}:w{weight}', config=config, weight=weight,
                                    inner_year=2000+inner_year,
                                    metrics=base.match_metrics(inner, (1-weight)*p+weight*market_inner), fit_audit=audit))
        trials.extend(current)
        selected = {family: min([trial for trial in current if trial['config']['family'] == family],
                                key=lambda t: (t['metrics']['logloss'], t['id']))
                    for family in ('independent', 'dc', 'recency', 'dc_recency')}
        pooled = min(current, key=lambda t: (t['metrics']['logloss'], t['id']))
        forecasts, audits = {'shin': market_outer}, {}
        for family, trial in selected.items():
            raw, audit = fit_predict(refit, outer, trial['config'])
            league_fits += 2
            forecasts[family+'_raw'] = raw
            forecasts[family] = base.validate((1-trial['weight'])*raw+trial['weight']*market_outer)
            audits[family] = audit
        forecasts['inner_selected'] = forecasts[pooled['config']['family']]
        ledgers = {name: {policy: base.settle(outer_all, base.choose_tickets(outer, p, policy))
                          for policy in base.POLICIES} for name, p in forecasts.items()}
        reports = {}
        for name, p in forecasts.items():
            comparison = 'independent_raw' if name.endswith('_raw') else 'independent'
            losses = {'shin': base.loss_ci(outer, p, market_outer),
                      comparison: base.loss_ci(outer, p, forecasts[comparison])}
            # The dependency names the generic comparator field minus_shin;
            # rename it in this new artifact rather than editing the dependency.
            for item in losses.values():
                item['difference'] = item.pop('minus_shin')
            policies = {}
            for policy in base.POLICIES:
                comparisons = {}
                for reference in ('shin', comparison):
                    ci = base.betting_ci(ledgers[name][policy], ledgers[reference][policy])
                    for item in ci.values():
                        item['difference'] = item.pop('minus_shin')
                    comparisons[reference] = ci
                policies[policy] = dict(metrics=base.betting_metrics(ledgers[name][policy]),
                                        ci=comparisons, ledger=ledgers[name][policy])
            reports[name] = dict(metrics=base.match_metrics(outer, p), loss_ci=losses, policies=policies)
        folds.append(dict(inner_year=2000+inner_year, outer_year=2000+outer_year,
                          train_matches=len(train), inner_matches=len(inner), refit_matches=len(refit),
                          outer_matches=len(outer), outer_actual_matches=len(outer_all),
                          selected_ids={family: trial['id'] for family, trial in selected.items()},
                          pooled_selected_id=pooled['id'], fit_audits=audits, reports=reports,
                          forecasts=[dict(**r, probabilities={name: p[i].tolist() for name, p in forecasts.items()})
                                     for i, r in enumerate(outer)]))
    paths = [Path(__file__).resolve(), ROOT/'scripts/local_score_model_tournament.py',
             ROOT/'src/score_dist.py', ROOT/'src/devig.py']
    return dict(status='exploratory exposed-data incremental ablation; NOT new holdout or actual Proto ROI',
                protocol_commit=protocol_commit, protocol_sha256_lf=hashlib.sha256(lf(PROTOCOL.read_bytes())).hexdigest(),
                code_sha256_lf={p.relative_to(ROOT).as_posix(): hashlib.sha256(lf(p.read_bytes())).hexdigest() for p in paths},
                seed=base.SEED, bootstrap_replicates=base.REPS, trial_count=len(trials), actual_league_fits=league_fits,
                sources=sources, optimizer_failures=[], versions=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                runtime_seconds=time.perf_counter()-started, trials=trials, folds=folds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/research/2026-10-01-dc-results.json')
    args = parser.parse_args()
    verify_protocol()
    rows, sources = base.load_sources()
    result = run(rows, sources)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('trial_count', 'actual_league_fits', 'runtime_seconds')}))
    for fold in result['folds']:
        print(fold['outer_year'], fold['selected_ids'], fold['pooled_selected_id'])
        print(json.dumps({name: data['metrics']['logloss'] for name, data in fold['reports'].items()}))


if __name__ == '__main__':
    main()
