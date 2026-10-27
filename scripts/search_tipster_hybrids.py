"""Offline screening of frozen paired records. Never promotes production models.

Input is PR354 paired_rows. Mixtures are scores, not calibrated probabilities.
Insufficient data returns no winner; synthetic tests are never empirical evidence.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path


def candidates():
    result = [dict(name='production', weight=0, kind='linear', shrink=0, gate=False),
              dict(name='tipster', weight=1, kind='linear', shrink=0, gate=False)]
    for kind in ('linear', 'logit'):
        for w in range(1, 20):
            for shrink in (0, 5, 20):
                for gate in (False, True):
                    result.append(dict(name=f'{kind}-w{w*5}-s{shrink}-g{int(gate)}',
                                       weight=w/20, kind=kind, shrink=shrink, gate=gate))
    return result


def probability(row, spec):
    model, crowd = row['arms']['production'], row['arms']['tipster']
    n = row['authors']
    crowd = (n*crowd + spec['shrink']*.5)/(n+spec['shrink'])
    w = spec['weight']
    # Disagreement gating conservatively halves tipster influence, not a bet filter.
    if spec['gate'] and (model-.5)*(crowd-.5)<0:
        w *= .5
    if spec['kind']=='linear':
        return (1-w)*model+w*crowd
    def logit(p):
        p = min(1-1e-6, max(1e-6, p))
        return math.log(p/(1-p))
    return 1/(1+math.exp(-((1-w)*logit(model)+w*logit(crowd))))


def measure(rows, spec):
    if not rows:
        return dict(n=0, hit_pct=None, roi_pct=None, brier=None)
    wins = profit = brier = 0
    for r in rows:
        p = probability(r, spec)
        side = 0 if p>=.5 else 1
        won = (side==0)==r['home_won']
        wins += won
        profit += r['odds'][side]-1 if won else -1
        brier += (p-r['home_won'])**2
    return dict(n=len(rows), hit_pct=100*wins/len(rows), roi_pct=100*profit/len(rows),
                brier=brier/len(rows))


def validate(rows):
    seen = set()
    for r in rows:
        if r['event'] in seen:
            raise ValueError('Duplicate event; authors are not independent games')
        seen.add(r['event'])
        at, snap, kick = (datetime.fromisoformat(r[k]) for k in ('at','snapshot_at','kickoff'))
        if any(t.tzinfo is None for t in (at,snap,kick)) or not snap<=at<=kick-timedelta(minutes=30):
            raise ValueError('Invalid pregame timing')
        if type(r['home_won']) is not bool or r['authors']<1:
            raise ValueError('Invalid outcome or author count')
        if len(r['odds'])!=2 or any(not math.isfinite(x) or x<=1 for x in r['odds']):
            raise ValueError('Invalid odds')
        for name in ('production','tipster'):
            if not math.isfinite(r['arms'][name]) or not 0<=r['arms'][name]<=1:
                raise ValueError('Invalid score')


def search(rows):
    validate(rows)
    specs = candidates()
    days = sorted({r['date'] for r in rows})
    # Fixed temporal partitions, no random splitting of correlated game days.
    a, b = len(days)*6//10, len(days)*8//10
    train = [r for r in rows if r['date'] in days[:a]]
    valid = [r for r in rows if r['date'] in days[a:b]]
    test = [r for r in rows if r['date'] in days[b:]]
    eligible = len(days)>=30 and min(map(len,(train,valid,test)))>=100
    result = dict(candidate_count=len(specs), paired_n=len(rows), day_count=len(days),
                  partitions=dict(train=len(train), validation=len(valid), test=len(test)),
                  status='screened_not_promoted' if eligible else 'blocked_insufficient_paired_data',
                  monthly_loss_limit_krw=50000, paper_policy_changed=False,
                  selection_rule='validation Brier ascending; ROI descending tie-break; name ascending',
                  selected=None, evaluation=None,
                  limitations=['Single-pick equal-stake diagnostic; not two-leg monthly paper profit.',
                               'Minimum counts are a screening guard, not proof of statistical significance.',
                               'Model/tipster overlap is required; never substitute a market proxy.',
                               'Hundreds of candidates require independent forward confirmation.',
                               'No sport-specific weights fit without sufficient per-sport data.'])
    if not eligible:
        return result
    ranked = sorted(specs, key=lambda s:(measure(valid,s)['brier'], -measure(valid,s)['roi_pct'], s['name']))
    chosen = ranked[0]
    result['selected'] = chosen
    # Only the selected candidate and two baselines are examined on final test.
    result['evaluation'] = {s['name']:measure(test,s) for s in [specs[0],specs[1],chosen]}
    result['validation_ranking'] = [dict(spec=s, metrics=measure(valid,s)) for s in ranked]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    raw = args.input.read_bytes()
    result = search(json.loads(raw)['paired_rows'])
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
