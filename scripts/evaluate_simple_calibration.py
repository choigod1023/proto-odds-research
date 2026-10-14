"""Exploratory chronological calibration; no production probability writes.

Requires an old, actually archived recommendation/odds pair and a later pair.
Never interprets missing probability provenance as market or model evidence.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from recommendation_history import settle_history
from roster_replay import replay


def stamp(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d if d.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def load_rows(payload, prices, at):
    for source in (payload, prices):
        generated = source.get('generated_at')
        if generated and (not stamp(generated) or stamp(generated) > at):
            raise ValueError('Snapshot is later than evaluation boundary or has invalid time')
    history = {}
    for row in (payload.get('recommendation_history') or {}).values():
        times = [stamp(row.get(k)) for k in ('kickoff_at','published_at','recorded_at')]
        try:
            p, price = float(row['probability']), float(row['odds'])
        except (KeyError, TypeError, ValueError):
            continue
        if (not row.get('id') or row.get('recommended') is not True or not all(times)
                or max(times[1:]) > at or max(times[1:]) >= times[0]-timedelta(minutes=30)
                or not np.isfinite([p,price]).all() or not 0 < p < 1 < price):
            continue
        if row['id'] in history:
            raise ValueError('Duplicate event ID')
        history[row['id']] = deepcopy(row)
        settled = stamp(row.get('settled_at'))
        if settled and settled > at:
            history[row['id']]['result'] = 'pending'
    settle_history(history, prices, at)
    return [r for r in history.values() if r.get('result_source') == 'official'
            and r.get('result') in ('hit','miss') and stamp(r['kickoff_at']) <= at]


def fit(rows):
    if len(rows) < 30 or len({r['result'] for r in rows}) < 2:
        return {'a': 1.0, 'b': 0.0, 'status': 'identity_insufficient_training'}
    x = logit(np.array([float(r['probability']) for r in rows]))
    y = np.array([r['result'] == 'hit' for r in rows], dtype=float)

    def objective(ab):
        a,b = ab
        z = a*x+b
        # Fixed shrinkage toward identity; do not tune using later ROI.
        loss = np.sum(np.logaddexp(0,z)-y*z) + (a-1)**2+b*b
        residual = expit(z)-y
        grad = np.array([np.dot(residual,x)+2*(a-1), np.sum(residual)+2*b])
        return loss,grad

    result = minimize(objective, [1.,0.], jac=True, method='L-BFGS-B', bounds=[(0,5),(-5,5)])
    if not result.success:
        raise RuntimeError('Calibration optimizer failed: '+str(result.message))
    return {'a': float(result.x[0]), 'b': float(result.x[1]), 'status': 'fitted'}


def predict(rows, model):
    return expit(model['a']*logit(np.array([float(r['probability']) for r in rows]))+model['b'])


def metrics(rows, probabilities):
    if not rows:
        return {'n': 0, 'brier': None, 'log_loss': None}
    y = np.array([r['result']=='hit' for r in rows], dtype=float)
    p = np.clip(probabilities,1e-12,1-1e-12)
    return {'n': len(rows), 'brier': float(np.mean((p-y)**2)),
            'log_loss': float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p)))}


def roi(rows, flags):
    chosen = [r for r, flag in zip(rows,flags) if flag]
    pnl = sum(float(r['odds'])-1 if r['result']=='hit' else -1 for r in chosen)
    return {'settled': len(chosen), 'hits': sum(r['result']=='hit' for r in chosen),
            'profit_units': pnl, 'roi': pnl/len(chosen) if chosen else None}


def compare(training_payload, training_odds, later_payload, later_odds, train_at, audit_at):
    if not train_at or not audit_at or train_at >= audit_at:
        raise ValueError('Invalid chronological boundaries')
    training = load_rows(training_payload,training_odds,train_at)
    seen = {r.get('id') for r in (training_payload.get('recommendation_history') or {}).values()}
    later = [r for r in load_rows(later_payload,later_odds,audit_at) if r['id'] not in seen
             and min(stamp(r[k]) for k in ('published_at','recorded_at')) > train_at]
    model = fit(training)
    raw = np.array([float(r['probability']) for r in later])
    calibrated = predict(later,model)
    prices = np.array([float(r['odds']) for r in later])
    # Outcomes may become available after kickoff. Use settlement timestamps,
    # not kickoff, for any inner temporal training split. Missing times excluded.
    folds = []
    dates = sorted({stamp(r['recorded_at']).date() for r in training})
    for index in sorted({len(dates)//2, 3*len(dates)//4}):
        if not dates or index >= len(dates):
            continue
        boundary = datetime.combine(dates[index], datetime.min.time(), tzinfo=train_at.tzinfo)
        past = [r for r in training if stamp(r.get('settled_at')) and stamp(r['settled_at']) < boundary]
        future = [r for r in training if stamp(r['recorded_at']) >= boundary]
        if len(past) < 30 or not future:
            folds.append({'boundary':boundary.isoformat(),'status':'insufficient_asof_settlements','training':len(past)})
            continue
        fitted = fit(past)
        folds.append({'boundary':boundary.isoformat(),'status':'exploratory_overlapping_windows',
                      'raw':metrics(future,[float(r['probability']) for r in future]),
                      'sigmoid':metrics(future,predict(future,fitted))})
    return {'training_n':len(training),'later_n':len(later),'model':model,
            'later_raw':metrics(later,raw),'later_sigmoid':metrics(later,calibrated),
            'later_p60':roi(later,(raw>=.6)&(prices>=1.5)),
            'later_calibrated_value_3pct':roi(later,calibrated*prices-1>=.03),
            'inner_temporal_checks':folds,
            'roster_replay':replay(later_payload,audit_at),
            'provenance':{
                'history':dict(Counter(r.get('probability_source') or 'unknown' for r in (later_payload.get('recommendation_history') or {}).values())),
                'current_candidates':dict(Counter(r.get('probability_source') or 'unknown' for r in later_payload.get('candidates') or [])),
                'complete_rosters':sum(r.get('roster_status')=='complete' for r in (later_payload.get('per_event_shadow',{}).get('records') or {}).values())},
            'promotion':'NOT_MET: exploratory, small later cohort, selected-pick training only',
            'limitations':['No provenance backfill from current candidates.',
                           'Selected-pick calibration cannot validate unselected alternative markets.',
                           'No retrospective two-leg ROI without complete simultaneous rosters.',
                           'Historical settlement refresh cannot reconstruct earlier result availability.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs',nargs=4,type=Path)
    parser.add_argument('--train-at',required=True)
    parser.add_argument('--audit-at',required=True)
    args = parser.parse_args()
    result = compare(*(json.loads(p.read_text(encoding='utf-8-sig')) for p in args.inputs),stamp(args.train_at),stamp(args.audit_at))
    result['train_at'],result['audit_at'] = args.train_at,args.audit_at
    result['sha256'] = [hashlib.sha256(p.read_bytes()).hexdigest() for p in args.inputs]
    print(json.dumps(result,ensure_ascii=True,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
