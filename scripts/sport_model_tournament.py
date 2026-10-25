"""Bounded offline sport tournament; never changes production policy.

Run prepare once, then one separate process per sport. Selection uses 2024 only.
2025 is reused research data, NOT an untouched prospective holdout.
"""
from __future__ import annotations
import os
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
import argparse
import hashlib
import json
import importlib.metadata
import platform
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax, logsumexp
from accuracy_formula_lab import fit_design, apply_design, date_block_interval
from sport_hybrid_validation import load_data, KEY, brier
from model_v2 import SPORT_FEATURES

SEED = 20261025
RULES = ('probability', 'positive_edge', 'wide_edge', 'high_confidence')
FORM = ['elo_diff', 'pi_diff', 'form_diff', 'margin_diff', 'trend_diff',
        'rest_diff', 'b2b_home', 'b2b_away', 'streak_diff', 'venue_diff', 'h2h_diff']


def catalog():
    specs = []
    for family, cols, offset in [('calibration', [], True),
                               ('rating_offset', ['elo_diff', 'pi_diff'], True),
                               ('form_offset', FORM, True), ('form_only', FORM, False)]:
        for ridge in ((.01,) if family == 'calibration' else (.001, .01)):
            specs.append(dict(name=f'{family}_{ridge}', cols=cols, offset=offset, ridge=ridge))
    for depth in (2, 4):
        specs.append(dict(name=f'tree_depth{depth}', cols=FORM, depth=depth))
    return specs


def names():
    return ['market'] + [f"{s['name']}_w{w}" for s in catalog() for w in (.25, .5, .75, 1.)]


def fit_predict(train, target, spec):
    x, state = fit_design(train, spec['cols'])
    xt = apply_design(target, state)
    market = np.asarray(train.market.tolist())
    mt = np.asarray(target.market.tolist())
    y = train.y.to_numpy(int)
    k = market.shape[1]
    if 'depth' in spec:
        from sklearn.ensemble import HistGradientBoostingClassifier
        model = HistGradientBoostingClassifier(max_depth=spec['depth'], max_iter=100,
                    learning_rate=.05, min_samples_leaf=50, l2_regularization=1., random_state=SEED)
        model.fit(np.column_stack([x, np.log(market)]), y)
        if list(model.classes_) != list(range(k)):
            raise ValueError('Missing training class')
        return model.predict_proba(np.column_stack([xt, np.log(mt)]))
    z = np.log(market) if spec['offset'] else np.zeros_like(market)
    target_y = np.eye(k)[y]
    ridge = spec['ridge']
    def objective(flat):
        beta = flat.reshape(x.shape[1], k)
        logits = z + x @ beta
        penalty = beta.copy(); penalty[0] = 0
        loss = (logsumexp(logits, axis=1) - logits[np.arange(len(y)), y]).mean()
        grad = x.T @ (softmax(logits, axis=1) - target_y) / len(y) + 2 * ridge * penalty
        return loss + ridge * (penalty ** 2).sum(), grad.ravel()
    result = minimize(objective, np.zeros(x.shape[1] * k), jac=True, method='L-BFGS-B',
                      options={'maxiter': 1000})
    if not result.success:
        raise RuntimeError(f"{spec['name']}: {result.message}")
    zt = np.log(mt) if spec['offset'] else 0
    return softmax(zt + xt @ result.x.reshape(x.shape[1], k), axis=1)


def predictions(train, target):
    market = np.asarray(target.market.tolist())
    result = {'market': market}
    for spec in catalog():
        p = fit_predict(train, target, spec)
        for w in (.25, .5, .75, 1.):
            result[f"{spec['name']}_w{w}"] = w * p + (1-w) * market
    for p in result.values():
        if not np.isfinite(p).all() or not np.allclose(p.sum(axis=1), 1.) or (p < 0).any():
            raise ValueError('Invalid probability')
    return result


def metrics(d, p):
    y = d.y.to_numpy(int); choice = p.argmax(axis=1)
    prices = np.asarray(d.odds.tolist())[np.arange(len(d)), choice]
    return dict(n=len(d), accuracy=float((choice == y).mean()),
                brier=float(brier(p, y).mean()),
                log_loss=float(-np.log(np.clip(p[np.arange(len(d)), y], 1e-12, 1)).mean()),
                all_match_single_roi=float((prices * (choice == y) - 1).mean()))


def select(validation_metrics, validation_paper):
    accuracy = min(validation_metrics, key=lambda n: (-validation_metrics[n]['accuracy'],
                                                      validation_metrics[n]['brier'], n))
    calibrated = min(validation_metrics, key=lambda n: (validation_metrics[n]['brier'], n))
    # Profit candidate must survive minimum exposure and six profitable months.
    eligible = [key for key, m in validation_paper.items() if m['tickets'] >= 30 and
                m['net'] > 0 and m['profitable_months'] >= 6]
    profit = max(eligible, key=lambda key: (validation_paper[key]['net'],
                  -validation_paper[key]['max_drawdown'], key)) if eligible else None
    return dict(accuracy=accuracy, calibration=calibrated, profit=profit)


def prepare(csv, output):
    if os.environ.get('PROODD_DB_PATH'):
        raise ValueError('Offline CSV only: unset PROODD_DB_PATH (do not print its value)')
    # Loader sorts historical matches before its day-batched feature updates.
    from matches import load_matches
    historical = load_matches(path=csv)
    if not historical.kickoff.is_monotonic_increasing:
        raise ValueError('Historical matches must be chronological before features')
    d, quality = load_data(csv)
    if d.duplicated(KEY).any() or not d.kickoff.is_monotonic_increasing:
        raise ValueError('Duplicate or unsorted joined events')
    for prices in d.odds:
        a = np.asarray(prices)
        if not np.isfinite(a).all() or (a <= 1).any():
            raise ValueError('Invalid historical odds')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    # Local trusted cache only, never accept an untrusted pickle.
    d.to_pickle(output / 'events.pkl')
    meta = dict(source_sha256=hashlib.sha256(Path(csv).read_bytes()).hexdigest(), quality=quality,
                counts=d.groupby(['sport', 'year']).size().to_dict())
    meta['counts'] = {f'{s}/{y}': n for (s, y), n in meta['counts'].items()}
    (output / 'source.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')


def run(cache, sport, output):
    from sport_tournament_paper import paper
    d = pd.read_pickle(Path(cache) / 'events.pkl')
    d = d[d.sport.eq(sport)].reset_index(drop=True)
    train, val, test = [d[d.year.eq(y)].reset_index(drop=True) for y in (2023, 2024, 2025)]
    if min(map(len, (train, val, test))) < 100:
        raise ValueError('Insufficient split')
    if not train.kickoff.max() < val.kickoff.min() or not val.kickoff.max() < test.kickoff.min():
        raise ValueError('Overlapping splits')
    vp = predictions(train, val)
    vm = {n: metrics(val, p) for n, p in vp.items()}
    vpaper = {}
    for n, p in vp.items():
        for rule in RULES:
            for weekly in (2, 3):
                m = paper(val, p, weekly, rule)
                vpaper[f'{n}|{rule}|{weekly}'] = {k: v for k, v in m.items() if k != 'tickets_detail'}
    chosen = select(vm, vpaper)
    # Freeze chosen IDs before generating any test model predictions.
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    (output / f'{sport}-selection.json').write_text(json.dumps(chosen, indent=2), encoding='utf-8')
    tp = predictions(pd.concat([train, val], ignore_index=True), test)
    # Fixed existing research reference, outside the newly searched catalog.
    reference_spec = dict(name='existing_research_reference',
                          cols=list(dict.fromkeys(['elo_diff', *SPORT_FEATURES[sport]])),
                          offset=True, ridge=.01)
    vm['existing_research_reference'] = metrics(val, fit_predict(train, val, reference_spec))
    tp['existing_research_reference'] = fit_predict(pd.concat([train, val], ignore_index=True), test, reference_spec)
    tm = {n: metrics(test, p) for n, p in tp.items()}
    selected_names = set(['market', 'existing_research_reference', chosen['accuracy'], chosen['calibration']])
    if chosen['profit']:
        selected_names.add(chosen['profit'].split('|')[0])
    selected = {}
    for n in sorted(selected_names):
        p = tp[n]; baseline = tp['market']; y = test.y.to_numpy(int)
        intervals = date_block_interval(pd.DataFrame(dict(date=test.date,
                    brier_delta=brier(p, y)-brier(baseline, y),
                    accuracy_delta=(p.argmax(1)==y).astype(float)-(baseline.argmax(1)==y).astype(float))),
                    ['brier_delta', 'accuracy_delta'], repeats=2000, seed=SEED, block_dates=7)
        selected[n] = dict(metrics=tm[n], intervals=intervals,
                          paper={f'{rule}|{w}': paper(test, p, w, rule) for rule in RULES for w in (2,3)},
                          leagues={str(l): metrics(test.iloc[idx], p[idx])
                                   for l, idx in test.groupby('league').indices.items()})
    result = dict(sport=sport, counts=dict(train=len(train), validation=len(val), test=len(test)),
                  source=json.loads((Path(cache) / 'source.json').read_text(encoding='utf-8')),
                  runtime=dict(python=platform.python_version(), packages={n: importlib.metadata.version(n)
                               for n in ('numpy', 'pandas', 'scipy', 'scikit-learn')}),
                  protocol_sha256=hashlib.sha256((ROOT / 'docs/research/2026-10-25-sport-tournament-protocol.md').read_bytes()).hexdigest(),
                  harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  model_count=len(vp), policy_config_count=len(vpaper), chosen=chosen,
                  validation_metrics=vm, validation_paper=vpaper, test_metrics=tm,
                  selected=selected, new_test_league_games=int((~test.league.isin(pd.concat([train, val]).league)).sum()),
                  production_changed=False, limitations=[
                      'Reused 2025 research data, not untouched holdout; existing feature choices also reused.',
                      'Historical odds capture times and sales rounds unavailable; tickets not proven purchasable.',
                      'Resolved archive only, excluded void/missing events; next-day settlement proxy.',
                      'Reconstructed baselines, not frozen production or tipster models.',
                      'Each sport is a separate hypothetical account; profits cannot be added under one loss cap.',
                      'Rolling features use previously observed games, not complete league histories.'])
    (output / f'{sport}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(sport=sport, chosen=chosen, counts=result['counts']), ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--prepare'); ap.add_argument('--cache', required=True)
    ap.add_argument('--sport', choices=['bs','sc','bk','vl']); ap.add_argument('--output')
    args = ap.parse_args()
    if args.prepare: prepare(args.prepare, args.cache)
    else: run(args.cache, args.sport, args.output)
