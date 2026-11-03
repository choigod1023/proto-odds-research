"""Prespecified NB2 count regression and intercept-only draw calibration."""
import copy
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import expit, logit
from scipy.stats import nbinom

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
import core

CONFIGS = {
    'negative_binomial_alpha01': {'family':'nb2', 'alpha':0.1},
    'negative_binomial_alpha03': {'family':'nb2', 'alpha':0.3},
    'draw_hurdle': {'family':'draw_hurdle', 'smoothing':0.5},
}
TAIL_TOL = 1e-13


def objective(beta, X, y, penalty, alpha):
    """NB2 negative log likelihood up to parameter-independent constants."""
    z = np.asarray(X @ beta).ravel()
    if np.max(np.abs(z)) > 30:
        return 1e100, np.zeros_like(beta)
    mu = np.exp(z)
    loss = np.sum((y + 1.0 / alpha) * np.log1p(alpha * mu) - y * z)
    loss += 0.5 * np.dot(penalty * beta, beta)
    grad = np.asarray(X.T @ ((mu - y) / (1.0 + alpha * mu))).ravel() + penalty * beta
    return float(loss), grad


def fit(raw_rows, labels, config_id):
    config = CONFIGS[config_id]
    rows = [core.normalize(r) for r in raw_rows]
    if not rows:
        raise ValueError('empty_training_population')
    if config['family'] == 'draw_hurdle':
        baseline = core.fit_baseline(raw_rows, labels)
        train_preds = core.predict_baseline(baseline, raw_rows)
        ds = np.array([p['probabilities_3way']['draw'] for p in train_preds])
        if np.any(ds <= 0) or np.any(ds >= 1):
            raise ValueError('invalid_baseline_draw_probability')
        draws = sum(labels[r['fixture_id']]['home_score'] == labels[r['fixture_id']]['away_score'] for r in rows)
        target = (draws + 0.5) / (len(rows) + 1)
        base_logits = logit(ds)
        offset = brentq(lambda x: float(np.mean(expit(base_logits + x)) - target), -100, 100, xtol=1e-14)
        return {'config_id':config_id, 'baseline':baseline, 'draw_intercept':float(offset),
                'training_draws':draws, 'n_train':len(rows), 'smoothed_draw_target':target,
                'training_draw_mean_after_calibration':float(np.mean(expit(base_logits + offset)))}
    spec = core.legacy.make_spec(rows)
    X,nbase = core.legacy.design(rows, spec, 'M0')
    y = core.legacy.target(rows, labels, 'M0')
    penalty = np.r_[0., np.full(nbase - 1,10.)]
    if X.shape[1] != nbase:
        raise ValueError('unexpected_M0_design')
    initial = np.zeros(X.shape[1])
    initial[0] = math.log(max(float(y.mean()),0.1))
    alpha = config['alpha']
    result = minimize(objective, initial, args=(X,y,penalty,alpha), jac=True, method='L-BFGS-B',
                      options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
    if not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all():
        raise ValueError('optimizer_failure:' + str(result.message))
    return {'config_id':config_id,'family':'nb2','alpha':alpha,'spec':spec,'beta':result.x.tolist(),
            'nbase':nbase,'objective':float(result.fun),'iterations':int(result.nit),
            'convergence':str(result.message),'gradient_max':float(np.max(np.abs(result.jac))),
            'n_train':len(rows),'penalty':'intercept=0, other M0 columns=10'}


def nb_outcomes(home_rates, away_rates, alpha, tail_tol=TAIL_TOL):
    """Independent NB2 score margins, summed over support with explicit tail bound."""
    h = np.asarray(home_rates, dtype=float)
    a = np.asarray(away_rates, dtype=float)
    if h.shape != a.shape or np.any(h <= 0) or np.any(a <= 0) or not np.isfinite(h).all() or not np.isfinite(a).all():
        raise ValueError('invalid_rates')
    n = 1.0 / alpha
    ph = 1.0 / (1.0 + alpha * h)
    pa = 1.0 / (1.0 + alpha * a)
    high = int(np.ceil(max(np.max(nbinom.isf(tail_tol / 2, n, ph)),
                           np.max(nbinom.isf(tail_tol / 2, n, pa)))))
    if high > 10000:
        raise ValueError('tail_support_excessive')
    values = np.arange(high + 1)
    hp = nbinom.pmf(values[None,:],n,ph[:,None])
    ap = nbinom.pmf(values[None,:],n,pa[:,None])
    ht = nbinom.sf(high,n,ph)
    at = nbinom.sf(high,n,pa)
    if np.max(ht+at) > tail_tol * 1.01:
        raise ValueError('tail_mass_exceeds_bound')
    h_before = np.cumsum(hp,axis=1) - hp
    a_before = np.cumsum(ap,axis=1) - ap
    probabilities = np.column_stack(((hp * a_before).sum(axis=1), (hp * ap).sum(axis=1), (ap * h_before).sum(axis=1)))
    mass = probabilities.sum(axis=1)
    if np.any(1 - mass > tail_tol * 1.05) or np.any(mass <= 0):
        raise ValueError('invalid_outcome_mass')
    return probabilities / mass[:,None], float(np.max(ht+at)), high


def predict(model, raw_rows):
    if model['config_id'] == 'draw_hurdle':
        baseline = core.predict_baseline(model['baseline'], raw_rows)
        out = []
        for original in baseline:
            row = copy.deepcopy(original)
            d0 = row['probabilities_3way']['draw']
            d = float(expit(logit(d0) + model['draw_intercept']))
            q = row['probabilities_2way']
            row['probabilities_3way'] = {'home':(1-d)*q['home'], 'draw':d, 'away':(1-d)*q['away']}
            out.append(row)
        return out
    rows = [core.normalize(r) for r in raw_rows]
    X, nbase = core.legacy.design(rows,model['spec'],'M0')
    if nbase != model['nbase']:
        raise ValueError('design_mismatch')
    z = np.asarray(X @ np.asarray(model['beta'])).ravel()
    if np.any(np.abs(z) > 20):
        raise ValueError('unstable_rate')
    rates = np.exp(z).reshape(-1,2)
    probabilities, tail_bound, high = nb_outcomes(rates[:,0],rates[:,1],model['alpha'])
    out = []
    for i,row in enumerate(rows):
        home,draw,away = probabilities[i]
        denom = home + away
        if denom <= 0:
            raise ValueError('no_decisive_probability')
        out.append({'fixture_id':row['fixture_id'], 'event_date':row['event_date'],
                    'probabilities_3way':{'home':float(home),'draw':float(draw),'away':float(away)},
                    'probabilities_2way':{'home':float(home/denom),'away':float(away/denom)},
                    'home_rate':float(rates[i,0]),'away_rate':float(rates[i,1]),
                    'roster_coverage':{side:core.legacy.side_features(row,side,model['spec']) for side in ('home','away')},
                    'tail_bound':tail_bound, 'score_support_max':high})
    return out
