"""Offline chronological calibration and context routing; no production writes.

No tuning on today's outcomes. Context weights use previously generated forward
predictions, not fitted in-sample probabilities. All thresholds are fixed here.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

WEIGHTS = (0., .25, .5, .75, 1.)
MIN_CALIBRATION = 20
MIN_CONTEXT = 20


def stamp(s):
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t


def available(row, at, day):
    return (row['date'] < day and row['y'] is not None and row.get('settled_at') is not None
            and stamp(row['settled_at']) < stamp(at))


def context(row):
    # Pregame-only variables; no lineup/stage claims where those fields are absent.
    agreement = (row['model'][0] >= .5) == (row['tipster'][0] >= .5)
    return (row['sport'], row['league'], 'agree' if agreement else 'disagree',
            'strong_favorite' if min(row['odds']) < 1.5 else 'balanced_price')


def fit_calibrator(past, field):
    if len(past) < MIN_CALIBRATION or len({r['date'] for r in past}) < 2:
        return (1., 0.), False
    p = np.clip([r[field][0] for r in past], .01, .99)
    x = np.log(p/(1-p))
    y = np.array([float(r['y'] == 0) for r in past])
    if len(set(y)) < 2:
        return (1., 0.), False
    # Regularized Platt map shrinks towards identity; coefficient settings fixed.
    def objective(ab):
        a,b = ab; z = a*x+b
        loss = np.logaddexp(0,z).sum() - np.dot(y,z) + 10*((a-1)**2+b*b)
        error = expit(z)-y
        return loss, np.array([np.dot(error,x)+20*(a-1), error.sum()+20*b])
    result = minimize(objective, [1.,0.], jac=True, bounds=[(.1,3.),(-2.,2.)], method='L-BFGS-B')
    if not result.success:
        raise RuntimeError(result.message)
    return tuple(float(x) for x in result.x), True


def calibrate(p, ab, fitted):
    if not fitted:
        return p
    q = np.clip(p,.01,.99)
    return float(expit(ab[0]*np.log(q/(1-q))+ab[1]))


def select_weight(history, row):
    past = [r for r in history if r['context']==context(row) and available(r,row['at'],row['date'])]
    if len(past)<MIN_CONTEXT or len({r['date'] for r in past})<3:
        return .25, len(past), 'insufficient_context_history'
    losses = {w:sum((r['candidates'][str(w)]-float(r['y']==0))**2 for r in past)/len(past) for w in WEIGHTS}
    best = min(WEIGHTS,key=lambda w:(losses[w],abs(w-.25)))
    if losses[.25]-losses[best] < .005:
        return .25,len(past),'no_minimum_brier_gain'
    return best,len(past),'past_prequential_brier_gain_only'


def validate(rows):
    seen=set()
    for r in rows:
        if r['id'] in seen:
            raise ValueError('Duplicate event')
        seen.add(r['id'])
        if not stamp(r['snapshot_at']) <= stamp(r['at']) < stamp(r['kickoff']):
            raise ValueError('Not pregame')
        if r.get('settled_at') and stamp(r['settled_at']) <= stamp(r['kickoff']):
            raise ValueError('Premature result')
        if r['y'] not in (0,1,None):
            raise ValueError('Binary result required')
        for field in ('model','tipster'):
            p=r[field]
            if len(p)!=2 or not all(np.isfinite(v) and 0<=v<=1 for v in p) or abs(sum(p)-1)>1e-6:
                raise ValueError('Binary probability vector required')


def forward(rows):
    validate(rows)
    rows=sorted(rows,key=lambda r:(stamp(r['at']),r['id']))
    output=[]
    for row in rows:
        past=[r for r in rows if r['sport']==row['sport'] and r['league']==row['league']
              and available(r,row['at'],row['date'])]
        ma,mfit=fit_calibrator(past,'model'); ta,tfit=fit_calibrator(past,'tipster')
        mp=calibrate(row['model'][0],ma,mfit); tp=calibrate(row['tipster'][0],ta,tfit)
        candidates={str(w):(1-w)*mp+w*tp for w in WEIGHTS}
        weight,n,reason=select_weight(output,row)
        output.append(dict(row,context=context(row),calibration_n=len(past),
            model_calibration=list(ma),tipster_calibration=list(ta),calibration_active=mfit and tfit,
            candidates=candidates,weight=weight,context_n=n,weight_reason=reason,
            forecasts={'model':row['model'][0], 'fixed75_25':.75*row['model'][0]+.25*row['tipster'][0],
                       'calibrated75_25':candidates['0.25'], 'context_hybrid':candidates[str(weight)]}))
    return output


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True);p.add_argument('--selector',type=Path,required=True)
    p.add_argument('--tipster-events',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    spec=importlib.util.spec_from_file_location('selector',a.selector);selector=importlib.util.module_from_spec(spec);spec.loader.exec_module(selector)
    doc=json.loads(a.input.read_text(encoding='utf8'))
    selector_key=next(k for k in doc['hashes'] if k.endswith('compare_selection_policies.py'))
    if hashlib.sha256(a.selector.read_bytes()).hexdigest()!=doc['hashes'][selector_key]:
        raise ValueError('Selector dependency changed')
    events_key=next(k for k in doc['hashes'] if k.endswith('tailslips_pick_events.jsonl'))
    if hashlib.sha256(a.tipster_events.read_bytes()).hexdigest()!=doc['hashes'][events_key]:
        raise ValueError('Settlement observation window changed')
    events=[json.loads(s) for s in a.tipster_events.read_text(encoding='utf8').splitlines()]
    cutoff=max(stamp(r['observed_at']) for r in events)
    rows=[r for r in doc['opportunity_ledger'] if r['authors']>=1]
    output=forward(rows)
    settled=[r for r in output if r['y'] is not None and stamp(r['settled_at'])<=cutoff]
    runs={}
    for name in ('model','fixed75_25','calibrated75_25','context_hybrid'):
        predictions=[dict(r,model=[r['forecasts'][name],1-r['forecasts'][name]]) for r in output]
        runs[name]={'brier_home':sum((r['forecasts'][name]-float(r['y']==0))**2 for r in settled)/len(settled),
                    'policies':selector.compare(predictions,cutoff)['model']}
    report={'status':'development_only_not_independent_holdout','monthly_loss_limit_krw':50000,
            'parameters':{'calibration_min':20,'calibration_dates':2,'identity_ridge':10,
                          'context_min':20,'context_dates':3,'min_brier_gain':.005,'weights':WEIGHTS},
            'coverage':{'candidates':len(rows),'settled':len(settled),'dates':len({r['date'] for r in rows}),
                        'calibration_active':sum(r['calibration_active'] for r in output),
                        'context_switches':sum(r['weight']!=.25 for r in output)},
            'weight_reasons':dict(Counter(r['weight_reason'] for r in output)),
            'runs':runs,'prediction_ledger':output,
            'limitations':['Five previously inspected MLB dates only; not independent new validation.',
                'No usable multisport pregame paired evidence: daily sport composition routing not evaluated.',
                'No lineup/injury/playoff features in input; no claims about their predictive effect.',
                'Publisher result labels, unproven quote availability, uncalibrated original crowd shares.',
                'Brier gain threshold is a heuristic, not statistical significance or ROI guarantee.',
                'Pair scores assume independence; short partial-month replay, no promotion or real wagers.'],
            'hashes':{str(f.resolve()):hashlib.sha256(f.read_bytes()).hexdigest() for f in (a.input,a.selector,a.tipster_events,Path(__file__))}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['coverage']))
    for name,r in runs.items():
        print(name,'brier',r['brier_home'])
        for policy,v in r['policies'].items():
            print(policy,v['recommendations'],v['settled'],v['single_roi_pct'],{w:{k:t[k] for k in ('tickets','settled','pending','net_krw','roi_pct')} for w,t in v['paper'].items()})


if __name__=='__main__':main()
