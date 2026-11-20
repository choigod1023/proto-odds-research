"""Prequential historical research: no production imports or policy promotion."""
import argparse
from collections import defaultdict,Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize

from matched_tipster_source import stamp,ET
from broaden_tipster_fixed91 import evaluate
from compare_matched_tipsters import single
from matched_tipster_replay import recommend

ARMS=('operating','raw91','cluster91','shrink91','calibrated_operating','calibrated_hybrid')
START='2026-09-05'
END='2026-09-13'
MIN_TRAIN=30
RIDGE=10.

def logit(p):
    p=max(.01,min(.99,float(p)))
    return math.log(p/(1-p))

def sigmoid(z):
    return 1/(1+math.exp(-max(-35,min(35,float(z)))))

def votes(row):
    out={}
    for t in row['tipster_evidence']:
        if t['author'] in out: raise ValueError('Duplicate author')
        if stamp(t['observed'])>stamp(row['at']): raise ValueError('Future tip')
        if t['side'] not in (row['home'],row['away']): raise ValueError('Wrong team')
        out[t['author']]=int(t['side']==row['home'])
    return out

def prior_rows(rows,cutoff):
    # Use only events whose prediction decision was already made.
    return [r for r in rows if stamp(r['at'])<cutoff]

def training_rows(rows,cutoff):
    return [r for r in prior_rows(rows,cutoff) if r.get('settled_at')
            and r.get('publisher_y') in (0,1)
            and stamp(r['kickoff'])<stamp(r['settled_at'])<cutoff]

def clusters(history):
    """High agreement is NOT proof of copied tips. Fixed sensitivity rule."""
    common=Counter();agree=Counter();authors=set()
    for r in history:
        v=votes(r);authors.update(v)
        names=sorted(v)
        for i,a in enumerate(names):
            for b in names[i+1:]:
                common[a,b]+=1;agree[a,b]+=v[a]==v[b]
    parent={a:a for a in authors}
    def root(a):
        while parent[a]!=a: a=parent[a]
        return a
    edges=[]
    for (a,b),n in sorted(common.items()):
        if n>=5 and agree[a,b]/n>=.9:
            left,right=root(a),root(b);parent[max(left,right)]=min(left,right)
            edges.append(dict(a=a,b=b,common=n,agreement=agree[a,b]))
    return {a:root(a) for a in authors},edges

def group_probability(row,mapping):
    grouped=defaultdict(list)
    for a,v in votes(row).items(): grouped[mapping.get(a,a)].append(v)
    if not grouped: raise ValueError('No votes')
    group_votes=[sum(v)/len(v) for v in grouped.values()]
    q=sum(group_votes)/len(group_votes);n=len(group_votes)
    shrunk=(n*q+5*row['model'][0])/(n+5)
    return q,shrunk,n

def fit(history,mapping,hybrid):
    if len(history)<MIN_TRAIN:
        return dict(a=0.,b=0.,trained=False,n=len(history),reason='minimum_train_not_met')
    offset=np.array([logit(r['model'][0]) for r in history])
    delta=np.array([logit(group_probability(r,mapping)[1])-logit(r['model'][0]) for r in history]) if hybrid else np.zeros(len(history))
    # Do not filter or repair these labels using later official outcomes.
    target=np.array([int(r['publisher_y']==0) for r in history])
    def loss(theta):
        z=offset+theta[0]+theta[1]*delta
        value=np.sum(np.logaddexp(0,z)-target*z)+.5*RIDGE*np.dot(theta,theta)
        residual=1/(1+np.exp(-z))-target
        gradient=np.array([residual.sum(),np.dot(residual,delta)])+RIDGE*theta
        return value,gradient
    opt=minimize(loss,np.zeros(2),jac=True,bounds=[(-1.,1.),(0.,1.) if hybrid else (0.,0.)],method='L-BFGS-B')
    if not opt.success: raise ValueError('Calibration optimizer failed: '+str(opt.message))
    return dict(a=float(opt.x[0]),b=float(opt.x[1]),trained=True,n=len(history),objective=float(opt.fun))

def predict(row,mapping,operating_fit,hybrid_fit):
    p=row['model'][0];v=votes(row);raw=sum(v.values())/len(v)
    q,shrunk,n=group_probability(row,mapping)
    return dict(operating=p,raw91=.9*p+.1*raw,cluster91=.9*p+.1*q,shrink91=.9*p+.1*shrunk,
                calibrated_operating=sigmoid(logit(p)+operating_fit['a']) if operating_fit['trained'] else p,
                calibrated_hybrid=sigmoid(logit(p)+hybrid_fit['a']+hybrid_fit['b']*(logit(shrunk)-logit(p))) if hybrid_fit['trained'] else .9*p+.1*shrunk),n

def forward(rows):
    ledgers={a:[] for a in ARMS};audit=[]
    dates=sorted({r['date'] for r in rows if START<=r['date']<=END})
    for day in dates:
        cutoff=datetime.fromisoformat(day).replace(tzinfo=ET)
        history=prior_rows(rows,cutoff);train=training_rows(rows,cutoff)
        mapping,edges=clusters(history)
        ofit=fit(train,mapping,False);hfit=fit(train,mapping,True)
        test=[r for r in rows if r['date']==day]
        if any(stamp(r['at'])<cutoff for r in test): raise ValueError('Decision before daily fit')
        audit.append(dict(day=day,cutoff=cutoff.isoformat(),history_ids=[r['id'] for r in history],
                          training_ids=[r['id'] for r in train],latest_training_settlement=max((r['settled_at'] for r in train),default=None),
                          test_ids=[r['id'] for r in test],group_map=mapping,agreement_edges=edges,
                          operating_fit=ofit,hybrid_fit=hfit))
        for r in test:
            predictions,n=predict(r,mapping,ofit,hfit)
            for name,p in predictions.items():
                ledgers[name].append(dict(r,model=[p,1-p],baseline_model=r['model'],effective_groups=n))
    return ledgers,audit

def metrics(rows):
    choices=[int(r['model'][1]>r['model'][0]) for r in rows]
    result=single(rows,choices)
    result['brier_home']=sum((r['model'][0]-int(r['y']==0))**2 for r in rows)/len(rows)
    result['log_loss']=sum(-math.log(max(1e-15,r['model'][r['y']])) for r in rows)/len(rows)
    cumulative=peak=drawdown=0.
    for r,s in sorted(zip(rows,choices),key=lambda x:(x[0]['kickoff'],x[0]['id'])):
        cumulative+=(r['odds'][s] if s==r['y'] else 0)-1
        peak=max(peak,cumulative);drawdown=max(drawdown,peak-cumulative)
    result['kickoff_order_drawdown_units']=drawdown
    result['drawdown_note']='Kickoff-ordered unit-return curve, not actual settlement cashflow.'
    recs=recommend(rows,'model','probability')
    result['filtered_single']=single(recs,[r['side'] for r in recs])
    result['daily']={d:single([r for r in rows if r['date']==d],[s for r,s in zip(rows,choices) if r['date']==d]) for d in sorted({r['date'] for r in rows})}
    # evaluate's operating arm uses supplied probabilities unchanged and proven offers.
    result['paper']=evaluate(rows)['operating']['conservative_paper']
    return result

def paired_uncertainty(baseline,candidate):
    if [r['id'] for r in baseline]!=[r['id'] for r in candidate]: raise ValueError('Unmatched evaluation')
    days=defaultdict(list)
    for a,b in zip(baseline,candidate):
        sa=int(a['model'][1]>a['model'][0]);sb=int(b['model'][1]>b['model'][0])
        days[a['date']].append((b['odds'][sb] if sb==b['y'] else 0)-(a['odds'][sa] if sa==a['y'] else 0))
    sums=np.array([sum(v) for v in days.values()]);counts=np.array([len(v) for v in days.values()])
    indices=np.random.default_rng(20261025).integers(0,len(days),size=(5000,len(days)))
    samples=100*sums[indices].sum(axis=1)/counts[indices].sum(axis=1)
    return dict(delta_roi_pp=float(100*sums.sum()/counts.sum()),
                descriptive_day_bootstrap_95pct=np.quantile(samples,[.025,.975]).tolist(),
                better_days=int((sums>1e-10).sum()),worse_days=int((sums< -1e-10).sum()),tied_days=int((np.abs(sums)<=1e-10).sum()),
                caveat='Only 9 days; no adjustment for prior model search, six comparisons or serial dependence.')

def run(source,destination):
    original=json.loads(source.read_text(encoding='utf8'));rows=original['expanded_ledger']
    ledgers,audit=forward(rows)
    results={name:metrics(items) for name,items in ledgers.items()}
    for name in ARMS:
        results[name]['vs_operating']=paired_uncertainty(ledgers['operating'],ledgers[name])
        results[name]['vs_raw91']=paired_uncertainty(ledgers['raw91'],ledgers[name])
    output=dict(protocol=dict(start=START,end=END,min_train=MIN_TRAIN,ridge=RIDGE,min_common=5,min_agreement=.9,
                              pseudocount=5,arms=ARMS,fit_cutoff='00:00 America/New_York; labels use publisher settlement observed strictly before cutoff, never later official labels'),
                input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                input_events=len(rows),evaluated_events=len(ledgers['operating']),folds=audit,results=results,ledgers=ledgers,
                limitations=['Already examined historical data; chronological replay is not untouched prospective validation.',
                             'All baseline operating values equal archived market probabilities, not independent trained model.',
                             'High agreement groups do not prove copied content; small overlapping samples can group coincidentally.',
                             'Only MLB moneyline. No other sports, seasons or new externally sourced games.',
                             'Monthly purchase stakes <=50000 KRW without recycling; not exact net-loss or settlement-month stop.',
                             'Published settlement observation supports retrospective label availability, not immutable independent timestamp proof.',
                             'Six prespecified arms reported without selecting/promoting winner on this evaluation.'])
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf8') as f:json.dump(output,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:{x:v[x] for x in ('settled','hits','roi_pct','brier_home','log_loss')} for k,v in results.items()}))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();run(a.input,a.output)
