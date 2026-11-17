"""Frozen MLB external check and separately fitted KBO/NPB common-input models."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
import argparse,hashlib,json,sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from build_mlb_bullpen_external import read

LOAD=['relief_outs1','relief_outs3','relief_consecutive','relief_top3_outs_used1']
QUALITY=LOAD+['relief_kbb_per_out28']
BUNDLES={'load':LOAD,'load_quality':QUALITY}


def market(r):
    if 'market_probability' in r:return r['market_probability']
    t=r.get('target')
    return market_probabilities(t['prices'])[t['order'].index('home')] if t else market_probabilities(r['historical_prices'])[0]


def reference(r):
    t=r.get('target');m=market(r)
    return .9*m+.1*t['vote_counts'].get('home',0)/t['voters'] if t else m


def objective(beta,x,y,offset,ridge):
    eta=offset+x@beta
    return (float(np.mean(np.logaddexp(0,eta)-y*eta)+ridge*beta@beta/2),x.T@(expit(eta)-y)/len(y)+ridge*beta)


def fit(rows,names,ridge):
    rows=[r for r in rows if r['hg']!=r['ag']]
    if len(rows)<50:raise ValueError('insufficient_non_draw_training')
    x=np.array([[r['features'][n] for n in names] for r in rows]);scale=np.where(x.std(axis=0)>1e-8,x.std(axis=0),1.)
    x=np.clip(x/scale,-6,6);offset=logit([market(r) for r in rows]);y=np.array([r['hg']>r['ag'] for r in rows],float)
    result=minimize(objective,np.zeros(len(names)),args=(x,y,offset,ridge),method='L-BFGS-B',jac=True,options=dict(maxiter=2000,gtol=1e-8,ftol=1e-11))
    if not result.success:raise ValueError(str(result.message))
    return dict(names=names,scale=scale.tolist(),beta=result.x.tolist(),ridge=ridge,train_n=len(rows),first=min(r['date'] for r in rows),last=max(r['date'] for r in rows))


def predict(config,rows,base=market):
    ps=np.array([base(r) for r in rows])
    if config is None or not rows:return ps
    z=np.clip(np.array([[r['features'][n] for n in config['names']] for r in rows])/np.array(config['scale']),-6,6)
    return expit(logit(ps)+z@np.array(config['beta']))


def metrics(rows,ps):
    if not rows:return dict(n=0,accuracy=None,roi=None)
    wins=push=0;net=Decimal(0);monthly=defaultdict(Decimal);daily=defaultdict(Decimal);probs=[];labels=[]
    for r,p in zip(rows,ps):
        t=r.get('target');prices=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away'];side='home' if p>=.5 else 'away'
        if r['hg']==r['ag']:push+=1;profit=Decimal(0)
        else:
            correct=(p>=.5)==(r['hg']>r['ag']);wins+=int(correct)
            profit=Decimal(str(prices[order.index(side)]))-1 if correct else Decimal(-1)
            probs.append(float(p));labels.append(int(r['hg']>r['ag']))
        net+=profit;monthly[r['date'][:7]]+=profit;daily[r['date']]+=profit
    peak=balance=drawdown=Decimal(0)
    for d in sorted(daily):balance+=daily[d];peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    p=np.clip(probs,1e-9,1-1e-9);y=np.array(labels)
    return dict(n=len(rows),wins=wins,pushes=push,accuracy=wins/(len(rows)-push) if len(rows)>push else None,
        roi=float(net/len(rows)),net_krw=int(net*10000),brier=float(np.mean((p-y)**2)) if len(y) else None,
        logloss=float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))) if len(y) else None,
        daily_close_max_drawdown_krw=int(drawdown*10000),monthly_units={k:float(v) for k,v in monthly.items()})


def interval(rows,p,q):
    groups=defaultdict(lambda:np.zeros(4))
    for r,a,b in zip(rows,p,q):
        d=date.fromisoformat(r['date']);week=(d-timedelta(days=d.weekday())).isoformat();draw=r['hg']==r['ag'];homewin=r['hg']>r['ag']
        t=r.get('target');prices=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away'];sides=[v>=.5 for v in [a,b]]
        hits=[int(s==homewin and not draw) for s in sides];nets=[0 if draw else prices[order.index('home' if s else 'away')]-1 if s==homewin else -1 for s in sides]
        groups[week]+=[hits[0]-hits[1],nets[0]-nets[1],int(not draw),1]
    if len(groups)<2:return dict(weeks=len(groups))
    values=np.array(list(groups.values()));rng=np.random.default_rng(20261029)
    sums=values[rng.integers(len(values),size=(3000,len(values)))].sum(axis=1);valid=sums[:,2]>0
    return dict(weeks=len(groups),accuracy_delta95=np.quantile(sums[valid,0]/sums[valid,2],[.025,.975]).tolist(),roi_delta95=np.quantile(sums[:,1]/sums[:,3],[.025,.975]).tolist())


def comparison(rows,config,base):
    p=predict(config,rows,base);q=predict(None,rows,base)
    return dict(baseline=metrics(rows,q),candidate=metrics(rows,p),changes=int(np.sum((p>=.5)!=(q>=.5))),interval=interval(rows,p,q))


def choose(models):return min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))


def run(folder):
    mlb=sorted(read(folder/'mlb-external-features.jsonl'),key=lambda r:(r['date'],r['gamePk']))
    frozen=json.loads((folder/'frozen-mlb-model.json').read_text(encoding='utf8'))['config'];result={'MLB_frozen_market_only':{}}
    ledger=[]
    for period,rows in {'all':mlb,'before_aug10':[r for r in mlb if r['date']<'2026-08-10'],'aug10_onward':[r for r in mlb if r['date']>='2026-08-10']}.items():
        result['MLB_frozen_market_only'][period]=comparison(rows,frozen,market)
    for r,p in zip(mlb,predict(frozen,mlb)):ledger.append(dict(r,baseline_probability=market(r),candidate_probability=float(p),model='frozen_MLB_bullpen_l1',reference_kind='market_only'))
    asian=read(folder/'asian-features.jsonl');fits={}
    for league in ['KBO','NPB']:
        train=[r for r in asian if r['league']==league and r['role']=='train'];val=[r for r in asian if r['league']==league and r['role']=='validation']
        evaluation=[r for r in asian if r['league']==league and r['role']=='evaluation']
        assert max(r['date'] for r in train)<min(r['date'] for r in val)<=max(r['date'] for r in val)<min(r['date'] for r in evaluation)
        models={}
        for bundle,names in BUNDLES.items():
            for ridge in [.01,.1,1.]:
                name=f'{bundle}:l{ridge:g}';selection=fit(train,names,ridge);final=fit(train+val,names,ridge)
                models[name]=dict(validation=metrics(val,predict(selection,val)),selection_fit=selection,final_fit=final,
                    evaluation=metrics(evaluation,predict(final,evaluation,reference)))
        models['market_null']=dict(validation=metrics(val,predict(None,val)),selection_fit=None,final_fit=None,
            evaluation=metrics(evaluation,predict(None,evaluation,reference)))
        chosen=choose(models);config=models[chosen]['final_fit'];fits[league]=dict(selected=chosen,models=models,train_n=len(train),validation_n=len(val),evaluation_n=len(evaluation))
        result[league]={}
        for period,rows in {'all':evaluation,'before_july':[r for r in evaluation if r['date']<'2026-07-01'],'july_onward':[r for r in evaluation if r['date']>='2026-07-01']}.items():result[league][period]=comparison(rows,config,reference)
        for r,p in zip(evaluation,predict(config,evaluation,reference)):ledger.append(dict(r,baseline_probability=reference(r),candidate_probability=float(p),model=chosen,reference_kind='market90_vote10'))
    report=dict(periods=result,asian_selection=fits,
        input_sha256={p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in [folder/'mlb-external-features.jsonl',folder/'asian-features.jsonl',folder/'frozen-mlb-model.json']})
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (folder/'predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ledger),encoding='utf8',newline='\n')
    print(json.dumps(dict(periods=result,selected={l:r['selected'] for l,r in fits.items()}),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);run(p.parse_args().folder)
