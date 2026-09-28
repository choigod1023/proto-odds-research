"""Frozen offline research protocol; no production imports that access storage.

See docs/CALIBRATION_UNDERDOG_PROTOCOL_20260928.md, committed before evaluation.
Run from the repository root: python scripts/evaluate_calibration_underdog.py
Optional --output writes the computed JSON report, never source CSVs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import logsumexp, softmax

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_external_temporal_roi import REV, download, features, fit, payout

POLICIES = ('p60_range', 'p60_low', 'ev03_all', 'ev03_nonfavorite', 'nonfavorite_control')
HASHES = {
    'results.csv': 'e3f80563eef122aa52cf5d9558ad41bae92e9a509abb00717864343795459d50',
    'results_with_odds.csv': '82337c0e7fb8de9bd39a75fcaf711b4895035edac8a94d2c791fbd0576441cee',
}


def arrays(rows):
    return (np.array([r['x'] for r in rows]),
            np.array([r['market'] for r in rows]),
            np.array([r['y'] for r in rows]))


def logloss(logits, y):
    return float((logsumexp(logits, axis=1)-logits[np.arange(len(y)), y]).mean())


def split_data(data):
    splits = {
        'train': [r for r in data if '2019-20' <= r['season'] <= '2022-23'],
        'calibration': [r for r in data if r['season'] == '2023-24'],
        'selection': [r for r in data if r['season'] == '2024-25'],
        'test': [r for r in data if r['season'] == '2025-26'],
    }
    if any(not rows for rows in splits.values()):
        raise ValueError('Missing temporal split')
    periods = list(splits.values())
    for earlier, later in zip(periods, periods[1:]):
        if max(r['day'] for r in earlier) >= min(r['day'] for r in later):
            raise ValueError('Temporal splits overlap')
    return splits


def train_models(train, calibration):
    """No access to selection/test rows during either fit or calibration."""
    w = fit(train)
    cx, cm, cy = arrays(calibration)
    temperature = minimize_scalar(lambda t: logloss(cx@w/t, cy),
                                  bounds=(.5, 2.), method='bounded')
    if not temperature.success:
        raise RuntimeError(temperature.message)
    x, market, y = arrays(train)
    x = x[:, :3]  # Constant, lagged Elo and goal form only; market is an offset.
    target = np.eye(3)[y]
    def residual_loss(flat):
        delta = flat.reshape(3, 3)
        logits = np.log(market)+x@delta
        loss = logloss(logits, y)+.1*np.square(delta).sum()/2
        grad = x.T@(softmax(logits, axis=1)-target)/len(y)+.1*delta
        return loss, grad.ravel()
    residual = minimize(residual_loss, np.zeros(9), jac=True, method='L-BFGS-B')
    if not residual.success:
        raise RuntimeError(residual.message)
    # Restricted classwise calibration, not full Dirichlet matrix calibration.
    def bias_loss(theta):
        logits = np.log(cm)/theta[0]+theta[1:]
        return logloss(logits, cy)+.1*np.square(theta[1:]).sum()/2
    bias = minimize(bias_loss, np.array([1., 0., 0., 0.]), method='L-BFGS-B',
                    bounds=[(.5, 2.), (None, None), (None, None), (None, None)])
    if not bias.success:
        raise RuntimeError(bias.message)
    return {'weights': w, 'temperature': float(temperature.x),
            'residual_weights': residual.x.reshape(3, 3), 'market_bias': bias.x}


def predict(models, rows):
    x = np.array([r['x'] for r in rows])
    market = np.array([r['market'] for r in rows])
    old = softmax(x@models['weights']/models['temperature'], axis=1)
    bias = models['market_bias']
    predictions = {
        'shin_market': market,
        'old_temperature': old,
        'half_market_blend': .5*old+.5*market,
        'market_residual': softmax(np.log(market)+x[:, :3]@models['residual_weights'], axis=1),
        'market_bias_calibration': softmax(np.log(market)/bias[0]+bias[1:], axis=1),
    }
    for p in predictions.values():
        if not np.isfinite(p).all() or (p <= 0).any() or not np.allclose(p.sum(axis=1), 1):
            raise ValueError('Invalid probability matrix')
    return predictions


def probability_metrics(rows, probs):
    y = np.array([r['y'] for r in rows])
    ll = -np.log(probs[np.arange(len(y)), y])
    return {'logloss': float(ll.mean()),
            'brier': float(np.square(probs-np.eye(3)[y]).sum(axis=1).mean()),
            'winner_accuracy': float((probs.argmax(axis=1) == y).mean())}


def ev_diagnostics(rows, probs):
    all_ev, nonfavorite_ev = [], []
    eligible_events, nonfavorite_events = set(), set()
    for row, p in zip(rows, probs):
        for prob, odds in zip(p, row['prices']):
            ev = float(prob*odds-1)
            all_ev.append(ev)
            if ev >= .03:
                eligible_events.add(row['match_id'])
            if odds > min(row['prices']):
                nonfavorite_ev.append(ev)
                if ev >= .03:
                    nonfavorite_events.add(row['match_id'])
    return {'max_ev_any_outcome': max(all_ev, default=None), 'max_ev_nonfavorite': max(nonfavorite_ev, default=None),
            'events_with_ev03_any': len(eligible_events),
            'events_with_ev03_nonfavorite': len(nonfavorite_events)}


def select_model(rows, predictions):
    scores = {name: probability_metrics(rows, p) for name, p in predictions.items()}
    return min(scores, key=lambda name: (scores[name]['logloss'], name)), scores


def choose_tickets(rows, probs, policy):
    """Select using features/prices only. Never read outcomes here."""
    if policy not in POLICIES:
        raise ValueError('Unknown policy')
    if np.shape(probs) != (len(rows), 3):
        raise ValueError('Probability length/shape mismatch')
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate match ID')
    if not np.isfinite(probs).all() or (np.asarray(probs) < 0).any() or not np.allclose(np.sum(probs, axis=1), 1):
        raise ValueError('Invalid probabilities')
    days = defaultdict(list)
    for row, p in zip(rows, probs):
        days[row['date']]
        odds = row['prices']
        if len(odds) != 3 or any(not np.isfinite(o) or o <= 1 for o in odds):
            raise ValueError('Invalid prices')
        options = []
        for i, (prob, price) in enumerate(zip(p, odds)):
            ev = float(prob*price-1)
            nonfavorite = price > min(odds)
            eligible = {
                'p60_range': prob >= .6 and 1.5 <= price < 2.2,
                'p60_low': prob >= .6 and price < 2.2,
                'ev03_all': ev >= .03,
                'ev03_nonfavorite': ev >= .03 and nonfavorite,
                'nonfavorite_control': nonfavorite,
            }[policy]
            if eligible:
                options.append({'id': row['match_id'], 'choice': i, 'probability': float(prob),
                                'odds': price, 'ev': ev, 'nonfavorite': nonfavorite})
        rank = lambda op: (-op['probability'], -op['ev'], op['choice']) if policy == 'nonfavorite_control' else (
            -op['ev'], -op['probability'], op['choice'])
        if options:
            days[row['date']].append(min(options, key=rank))
    tickets = {}
    for day, options in sorted(days.items()):
        rank = lambda op: (-op['probability'], -op['ev'], op['id']) if policy == 'nonfavorite_control' else (
            -op['ev'], -op['probability'], op['id'])
        tickets[day] = sorted(options, key=rank)[:2] if len(options) >= 2 else []
    return tickets


def settle(rows, tickets):
    outcomes = {r['match_id']: r['y'] for r in rows}
    daily = []
    for day, legs in tickets.items():
        if not legs:
            daily.append({'date': day, 'stake': 0, 'raw': 0., 'rounded': 0., 'won': 0})
            continue
        if len(legs) != 2 or legs[0]['id'] == legs[1]['id']:
            raise ValueError('A ticket requires two distinct matches')
        won = int(all(outcomes[leg['id']] == leg['choice'] for leg in legs))
        raw_payout = legs[0]['odds']*legs[1]['odds']
        rounded = payout(legs[0]['odds'], legs[1]['odds'])
        daily.append({'date': day, 'stake': 1, 'won': won,
                      'raw': raw_payout*won-1, 'rounded': rounded*won-1})
    return daily


def week_key(day):
    iso = date.fromisoformat(day).isocalendar()
    return iso.year, iso.week


def week_matrix(records, fields):
    grouped = defaultdict(lambda: np.zeros(len(fields)))
    for row in records:
        grouped[week_key(row['date'])] += [row[field] for field in fields]
    return sorted(grouped), np.array([grouped[key] for key in sorted(grouped)])


def cluster_interval(records, numerator, denominator):
    _, matrix = week_matrix(records, (numerator, denominator))
    if not len(matrix) or matrix[:, 1].sum() == 0:
        return None
    rng = np.random.default_rng(20260928)
    sampled = matrix[rng.integers(0, len(matrix), (5000, len(matrix)))].sum(axis=1)
    valid = sampled[:, 1] > 0
    return np.percentile(sampled[valid, 0]/sampled[valid, 1], [2.5, 97.5]).tolist()


def summarize(rows, tickets, daily):
    n = sum(r['stake'] for r in daily)
    wins = sum(r['won'] for r in daily)
    profit = sum(r['raw'] for r in daily)
    rounded = sum(r['rounded'] for r in daily)
    path = np.r_[0., np.cumsum([r['raw'] for r in daily])]
    legs = [leg for pair in tickets.values() for leg in pair]
    outcomes = {r['match_id']: r['y'] for r in rows}
    expected = [a['probability']*b['probability']*a['odds']*b['odds']-1
                for pair in tickets.values() if len(pair) == 2 for a, b in [pair]]
    return {'tickets': n, 'wins': wins, 'hit_rate': wins/n if n else None,
            'roi_raw': profit/n if n else None, 'roi_rounded': rounded/n if n else None,
            'roi_raw_ci95_week_cluster': cluster_interval(daily, 'raw', 'stake'),
            'profit_raw_units': profit, 'profit_rounded_units': rounded,
            'budget_days': len(daily), 'budget_return_raw': profit/len(daily) if daily else None,
            'max_drawdown_raw_units': float((np.maximum.accumulate(path)-path).max()),
            'nonfavorite_legs': sum(leg['nonfavorite'] for leg in legs),
            'low_odds_legs': sum(leg['odds'] < 1.5 for leg in legs),
            'selected_leg_hit_rate': sum(outcomes[leg['id']] == leg['choice'] for leg in legs)/len(legs) if legs else None,
            'expected_roi_raw_independence_assumption': float(np.mean(expected)) if expected else None,
            'selection_sha256': hashlib.sha256(json.dumps(tickets, sort_keys=True).encode()).hexdigest()}


def paired_budget_delta(candidate, baseline):
    if [r['date'] for r in candidate] != [r['date'] for r in baseline]:
        raise ValueError('Paired comparison date mismatch')
    records = [{'date': a['date'], 'delta': a['raw']-b['raw'], 'budget': 1}
               for a, b in zip(candidate, baseline)]
    return {'budget_return_difference': sum(r['delta'] for r in records)/len(records),
            'ci95_week_cluster': cluster_interval(records, 'delta', 'budget')}


def run():
    source, hashes, counts = download(last_season='2025-26')
    if hashes != HASHES:
        raise ValueError('Pinned data bytes changed')
    data = features(source)
    splits = split_data(data)
    if [len(splits[k]) for k in splits] != [1520, 380, 380, 380]:
        raise ValueError('Unexpected split coverage/missing odds')
    models = train_models(splits['train'], splits['calibration'])
    chosen, validation = select_model(splits['selection'], predict(models, splits['selection']))
    test = splits['test']
    predictions = predict(models, test)
    baseline = settle(test, choose_tickets(test, predictions['old_temperature'], 'p60_range'))
    report = {'source_commit': REV, 'source_hashes': hashes, 'season_counts': counts,
              'split_counts': {key: len(value) for key, value in splits.items()},
              'protocol_commit': '1b7cd36d', 'selected_before_test': chosen,
              'validation_2024_25': validation, 'test_2025_26': {},
              'parameters': {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in models.items()}}
    for name, p in predictions.items():
        metrics = probability_metrics(test, p)
        y = np.array([r['y'] for r in test])
        loss_delta = -np.log(p[np.arange(len(y)), y])+np.log(predictions['shin_market'][np.arange(len(y)), y])
        metrics['logloss_delta_vs_market_ci95_week'] = cluster_interval(
            [{'date': r['date'], 'delta': float(delta), 'count': 1} for r, delta in zip(test, loss_delta)], 'delta', 'count')
        results = {}
        for policy in POLICIES:
            tickets = choose_tickets(test, p, policy)
            daily = settle(test, tickets)
            summary = summarize(test, tickets, daily)
            summary['vs_old_temperature_p60_range'] = paired_budget_delta(daily, baseline)
            results[policy] = summary
        report['test_2025_26'][name] = {'probabilities': metrics, 'policies': results,
                                       'ev_diagnostics': ev_diagnostics(test, p)}
    selected = report['test_2025_26'][chosen]
    market_ll = report['test_2025_26']['shin_market']['probabilities']['logloss']
    promising = []
    for policy, result in selected['policies'].items():
        ci = result['roi_raw_ci95_week_cluster']
        delta_ci = result['vs_old_temperature_p60_range']['ci95_week_cluster']
        if (policy != 'nonfavorite_control' and result['tickets'] >= 100 and ci and ci[0] > 0
                and delta_ci and delta_ci[0] > 0 and selected['probabilities']['logloss'] <= market_ll):
            promising.append(policy)
    report['independent_validation_candidates'] = promising
    report['production_policy_changed'] = False
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = run()
    serialized = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(serialized+'\n', encoding='utf-8')
        print(json.dumps({'output': str(args.output), 'selected_before_test': report['selected_before_test'],
                          'independent_validation_candidates': report['independent_validation_candidates']}))
    else:
        print(serialized)
