"""Offline frozen-rule comparison; never changes production recommendations.

The challenger is exploratory, not a preregistered prospective experiment.
Inputs must be immutable snapshots actually collected at the supplied times.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from recommendation_history import settle_history


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def eligible(row, now):
    times = [timestamp(row.get(k)) for k in ('kickoff_at', 'published_at', 'recorded_at')]
    try:
        p, odds = float(row['probability']), float(row['odds'])
    except (KeyError, ValueError, TypeError):
        return False
    return bool(row.get('id') and row.get('recommended') is True
                and all(times) and max(times[1:]) <= now
                and max(times[1:]) < times[0] - timedelta(minutes=30)
                and math.isfinite(p) and math.isfinite(odds) and 0 < p < 1 < odds)


def rows_from(payload, prices, now):
    for source in (payload, prices):
        generated = source.get('generated_at')
        if generated and (timestamp(generated) is None or timestamp(generated) > now):
            raise ValueError('snapshot generated_at is invalid or later than audit boundary')
    archive = payload.get('recommendation_history') or {}
    if not isinstance(archive, dict):
        raise ValueError('recommendation_history must be an object')
    result = {}
    for row in archive.values():
        if not isinstance(row, dict) or not eligible(row, now):
            continue
        if row['id'] in result:
            raise ValueError('duplicate event id: ambiguous snapshot')
        result[row['id']] = deepcopy(row)
        settled_at = timestamp(row.get('settled_at'))
        if settled_at and settled_at > now:
            result[row['id']]['result'] = 'pending'
    settle_history(result, prices, now)
    for row in result.values():
        if row.get('result_source') != 'official' or timestamp(row['kickoff_at']) > now:
            row['result'] = 'pending'
    return list(result.values())


def pnl(row):
    return float(row['odds']) - 1 if row['result'] == 'hit' else (-1 if row['result'] == 'miss' else 0)


def wilson_lower(hits, n):
    """95% Wilson lower endpoint, descriptive: correlated bets weaken coverage."""
    if not n:
        return 0.0
    z = 1.959963984540054
    p = hits / n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)


def bucket(row):
    # Fixed 5-percentage-point bins, no search over bin widths or thresholds.
    return (str(row.get('sport')), str(row.get('market')), math.floor(float(row['probability'])*20))


def fit(rows):
    bins = defaultdict(Counter)
    for row in rows:
        if row.get('result') in ('hit', 'miss'):
            bins[bucket(row)]['n'] += 1
            bins[bucket(row)]['hits'] += row['result'] == 'hit'
    return dict(bins)


def challenger(row, model):
    stats = model.get(bucket(row), {})
    n = stats.get('n', 0)
    return n >= 30 and wilson_lower(stats.get('hits', 0), n)*float(row['odds']) > 1.0


def calibrated_probability(row, model):
    """Exploratory partial pooling: 20 pseudo-observations from OTHER cells.

    Constants are not optimized by this evaluator. This is an empirical score,
    not a validated probability or a conservative confidence bound.
    """
    key = bucket(row)
    local = model.get(key, {})
    others = [s for k, s in model.items() if k[2] == key[2] and k != key]
    other_n = sum(s['n'] for s in others)
    local_n = local.get('n', 0)
    if local_n + other_n < 30:
        return None
    if not other_n:
        return local['hits']/local_n
    prior = sum(s['hits'] for s in others)/other_n
    return (local.get('hits', 0) + 20*prior)/(local_n+20)


def pooled_value(row, model):
    p = calibrated_probability(row, model)
    return p is not None and p*float(row['odds']) - 1 >= .03


def summary(rows):
    settled = [r for r in rows if r.get('result') in ('hit', 'miss')]
    count = len(settled)
    profit = sum(map(pnl, settled))
    void = sum(r.get('result') == 'void' for r in rows)
    return dict(selected=len(rows), settled=count, hits=sum(r['result']=='hit' for r in settled),
                void=void, pending=len(rows)-count-void, profit_units=profit,
                roi=profit/count if count else None,
                roi_including_void_stakes=profit/(count+void) if count+void else None,
                mean_saved_ev=sum(float(r['probability'])*float(r['odds'])-1 for r in settled)/count if count else None)


def evaluate(training, evaluation):
    model = fit(training)
    policies = {'all': lambda r: True,
                'p58_o150': lambda r: float(r['probability']) >= .58 and float(r['odds']) >= 1.5,
                'p60_o150': lambda r: float(r['probability']) >= .60 and float(r['odds']) >= 1.5,
                'sport_market_wilson_value_v1': lambda r: challenger(r, model),
                'partial_pooling_value_v1': lambda r: pooled_value(r, model)}
    return {name: summary([r for r in evaluation if rule(r)]) for name, rule in policies.items()}


def compare(old, old_prices, current, prices, train_at, now):
    if not train_at or not now or train_at >= now:
        raise ValueError('timezone-aware train_at must precede audit_at')
    training = rows_from(old, old_prices, train_at)
    observed = rows_from(current, prices, now)
    # Exclude ALL previously visible IDs, including non-recommended/pending ones.
    old_ids = {r.get('id') for r in (old.get('recommendation_history') or {}).values() if isinstance(r, dict)}
    evaluation = [r for r in observed if r['id'] not in old_ids
                  and min(timestamp(r[k]) for k in ('published_at', 'recorded_at')) > train_at]
    days = sorted({timestamp(r['kickoff_at']).date().isoformat() for r in evaluation})
    model = fit(training)
    scored = [(r, calibrated_probability(r, model)) for r in evaluation if r.get('result') in ('hit','miss')]
    scored = [(r, p) for r,p in scored if p is not None]
    calibration = {'n': len(scored),
                   'raw_brier': sum((float(r['probability'])-(r['result']=='hit'))**2 for r,p in scored)/len(scored) if scored else None,
                   'pooled_brier': sum((p-(r['result']=='hit'))**2 for r,p in scored)/len(scored) if scored else None}
    return {'train_snapshot_at': train_at.isoformat(), 'audit_at': now.isoformat(),
            'current_artifact_generated_at': current.get('generated_at'),
            'training': evaluate(training, training), 'later_unseen_events': evaluate(training, evaluation),
            'later_kickoff_dates_as_recorded': days,
            'later_calibration_same_rows': calibration,
            'evaluation_rows_excluded_as_previously_observed': sum(r['id'] in old_ids for r in observed),
            'trained_bins': [{'sport': k[0], 'market': k[1], 'probability_bin_lower': k[2]/20,
                              **v, 'wilson_lower': wilson_lower(v['hits'],v['n'])}
                             for k,v in sorted(fit(training).items())],
            'promotion': 'NOT_MET: exploratory challenger; later cohort small; no prospective validation',
            'combo_roi': None,
            'limitations': ['Saved final T30 picks do not prove simultaneous fresh prices for two legs.',
                            'Training statistics are descriptive, not cross-validation.',
                            'Wilson bounds assume independent trials; no guarantee for correlated bets.',
                            'Realized ROI is not future expected ROI. No production change.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('training_recommendations', 'training_odds', 'recommendations', 'odds'):
        p.add_argument(name, type=Path)
    p.add_argument('--train-snapshot-at', required=True)
    p.add_argument('--audit-at', required=True)
    args = p.parse_args()
    paths = [getattr(args, n) for n in ('training_recommendations','training_odds','recommendations','odds')]
    result = compare(*(json.loads(f.read_text(encoding='utf-8-sig')) for f in paths),
                     timestamp(args.train_snapshot_at), timestamp(args.audit_at))
    result['input_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for name,path in zip(('training_recommendations','training_odds','recommendations','odds'),paths)}
    # ASCII JSON survives Windows console code pages without corrupting labels.
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
