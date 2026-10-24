"""Offline probability/value separation with chronological return learning."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sport_hybrid_validation import load_data, fit, predict
from model_v2 import SPORT_FEATURES

POLICIES=('market','probability','value','hybrid')


def design(p,q,odds):
    n,k=p.shape
    return np.column_stack([np.ones(n*k),p.ravel(),q.ravel(),np.log(odds).ravel(),
                            (p-q).ravel(),(p*p).ravel(),np.tile(np.eye(k),(n,1))])


def fit_value(p,q,odds,y):
    x=design(p,q,odds); ev=p*odds-1
    target=(np.eye(p.shape[1])[np.asarray(y,int)]*odds-1-ev).ravel()
    weights=1/odds.ravel()**2
    penalty=np.eye(x.shape[1])*10;penalty[0,0]=0
    return np.linalg.solve(x.T@(weights[:,None]*x)+penalty,x.T@(weights*target))


def value_predict(beta,p,q,odds):
    ev=p*odds-1
    correction=(design(p,q,odds)@beta).reshape(p.shape)
    return np.clip(ev+.5*correction,-.95,odds-1)


def forecasts(d):
    frames=[]; metadata=[]
    for sport,g in d.groupby('sport'):
        cols=list(dict.fromkeys(['elo_diff',*SPORT_FEATURES[sport]]))
        for year in (2025,2026):
            train=g[g.year<year-1]; cal=g[g.year==year-1]; test=g[g.year==year]
            assert train.date.max()<cal.date.min()<=cal.date.max()<test.date.min()
            cp=predict(fit(train,cols,True),cal)
            beta=fit_value(cp,np.array(cal.market.tolist()),np.array(cal.odds.tolist()),cal.y)
            p=predict(fit(pd.concat([train,cal]),cols,True),test)
            q=np.array(test.market.tolist()); odds=np.array(test.odds.tolist())
            v=value_predict(beta,p,q,odds)
            out=test[['date','kickoff','sport','league','home_team','away_team','y','odds','market']].copy()
            out['probability']=list(p);out['value']=list(v);frames.append(out)
            metadata.append(dict(sport=sport,year=year,train=len(train),value_train=len(cal),test=len(test)))
            print(sport,year,'forecasts',len(test),flush=True)
    return pd.concat(frames,ignore_index=True),metadata


def options(d):
    rows=[]
    for i,r in d.iterrows():
        for k,price in enumerate(r.odds):
            p=float(r.probability[k]);q=float(r.market[k]);v=float(r.value[k])
            rows.append(dict(event=int(i),date=r.date,sport=r.sport,league=r.league,
                home=r.home_team,away=r.away_team,choice=k,price=price,p=p,q=q,v=v,
                ev=p*price-1,hit=int(r.y==k)))
    return pd.DataFrame(rows)


def compatible(a,b):
    if a.event==b.event: return False
    return (a.sport,a.league)!=(b.sport,b.league) or not ({a.home,a.away}&{b.home,b.away})


def select_pairs(o,policy,abstain=False):
    a=o.copy()
    if abstain:
        if policy=='market': a=a[a.q*a.price>1]
        elif policy=='probability': a=a[a.ev>0]
        elif policy=='value': a=a[a.v>0]
        else: a=a[(a.p>=.55)&(a.ev>0)&(a.v>0)]
    scores={'market':np.log(a.q),'probability':np.log(a.p),
            'value':np.log1p(a.v),'hybrid':.5*np.log(a.p)+.5*np.log1p(a.v)}
    a['score']=scores[policy]; tickets=[]
    for day,g in a.groupby('date',sort=True):
        rows=list(g.sort_values(['score','event','choice'],ascending=[False,True,True]).itertuples())
        best=None;best_score=-np.inf
        for i,x in enumerate(rows):
            if i+1>=len(rows) or x.score+rows[i+1].score<=best_score: break
            for z in rows[i+1:]:
                if x.score+z.score<=best_score: break
                if compatible(x,z): best=(x,z);best_score=x.score+z.score;break
        if best is None: continue
        x,z=best; price=x.price*z.price; hit=x.hit*z.hit
        tickets.append(dict(date=str(day.date()),events=[x.event,z.event],choices=[x.choice,z.choice],
            sports=[x.sport,z.sport],leg_hits=x.hit+z.hit,hit=hit,odds=price,
            teams=[[x.league,x.home,x.away],[z.league,z.home,z.away]],
            expected_hit=x.p*z.p,return_=price*hit-1))
    return tickets


def describe(tickets):
    n=len(tickets)
    return dict(tickets=n,pair_accuracy=sum(t['hit'] for t in tickets)/n if n else None,
        leg_accuracy=sum(t['leg_hits'] for t in tickets)/(2*n) if n else None,
        roi=sum(t['return_'] for t in tickets)/n if n else None,
        average_odds=sum(t['odds'] for t in tickets)/n if n else None)


def simulate(tickets,weekly,months):
    profits={m:0. for m in months};weeks=defaultdict(int);kept=[]
    for t in tickets:
        day=pd.Timestamp(t['date']);month=str(day.to_period('M'));week=tuple(day.isocalendar()[:2])
        if profits[month]-10000 < -50000-1e-8 or weeks[week]>=weekly:continue
        profits[month]+=10000*t['return_'];weeks[week]+=1;kept.append(t)
    assert min(profits.values(),default=0)>=-50000-1e-7
    pnl=[t['return_']*10000 for t in kept]
    return dict(**describe(kept),net=round(sum(pnl)),monthly_net={m:round(v) for m,v in profits.items()},
        positive_months=sum(v>0 for v in profits.values()),months=len(profits),
        worst_month=round(min(profits.values(),default=0)),
        net_without_best_ticket=round(sum(pnl)-max(pnl)) if pnl else None)


def inference(tickets):
    common=sorted(set.intersection(*(set(t['date'] for t in ts) for ts in tickets.values())))
    aligned={k:{t['date']:t for t in ts} for k,ts in tickets.items()}
    if not common:return [],{}
    metrics={k:describe([v[d] for d in common]) for k,v in aligned.items()}
    tests=[];rng=np.random.default_rng(20261024);n=len(common);width=min(7,n)
    for policy in POLICIES[1:]:
        for field in ('hit','return_'):
            diff=np.array([aligned[policy][d][field]-aligned['market'][d][field] for d in common])
            samples=[]
            for _ in range(20):
                starts=rng.integers(n,size=(500,int(np.ceil(n/width))))
                idx=((starts[:,:,None]+np.arange(width))%n).reshape(500,-1)[:,:n]
                samples.extend(diff[idx].mean(1))
            samples=np.array(samples);delta=float(diff.mean())
            tests.append(dict(policy=policy,endpoint=field,delta=delta,ci=np.quantile(samples,[.025,.975]).tolist(),
                p=float((1+(np.abs(samples-delta)>=abs(delta)-1e-15).sum())/(len(samples)+1))))
    return tests,metrics


def run(path):
    digest=hashlib.sha256(path.read_bytes()).hexdigest();d,quality=load_data(path)
    f,folds=forecasts(d);o=options(f);result=dict(source_sha256=digest,quality=quality,folds=folds,groups={},tests=[])
    ledger={}
    for group in ['pooled','bk','bs','sc','vl']:
        subset=o if group=='pooled' else o[o.sport==group]
        months=sorted(subset.date.dt.to_period('M').astype(str).unique())
        forced={p:select_pairs(subset,p) for p in POLICIES}
        abstained={p:select_pairs(subset,p,True) for p in POLICIES}
        tests,metrics=inference(forced)
        result['tests'].extend(dict(group=group,**t) for t in tests)
        result['groups'][group]=dict(equal_count=metrics,
            years={str(y):{p:describe([t for t in ts if t['date'].startswith(str(y))]) for p,ts in forced.items()} for y in (2025,2026)},
            capped={mode:{p:{str(w):simulate(ts,w,months) for w in (2,3)} for p,ts in sets.items()}
                    for mode,sets in [('forced',forced),('abstain',abstained)]})
        ledger[group]=dict(forced=forced,abstain=abstained)
        print(group,metrics,flush=True)
    order=sorted(range(len(result['tests'])),key=lambda i:result['tests'][i]['p']);prev=0
    for rank,i in enumerate(order):
        prev=max(prev,min(1,(len(order)-rank)*result['tests'][i]['p']))
        result['tests'][i]['holm_p']=prev
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    return result,ledger


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--games',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();r,ledger=run(args.games);args.output.mkdir(parents=True,exist_ok=True)
    for name,data in [('results',r),('tickets',ledger)]:
        (args.output/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
