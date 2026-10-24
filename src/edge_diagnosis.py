"""Diagnose calibration/market edge and validate accuracy-constrained selection."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax,logsumexp
from sport_hybrid_validation import load_data,fit,predict,brier
from dual_objective import options,select_pairs,describe,simulate
from model_v2 import SPORT_FEATURES

GRID=(0.,.1,.25,.5,1.)
ALLOWANCES=(0.,.01,.02)


def calibrate_fit(p,y):
    logp=np.log(np.clip(p,1e-12,1));y=np.asarray(y,int);k=p.shape[1]
    def loss(theta):
        logits=logp*np.exp(theta[0])+theta[1:]
        return float((logsumexp(logits,axis=1)-logits[np.arange(len(y)),y]).mean()+.01*np.sum(theta**2))
    r=minimize(loss,np.zeros(k+1),method='L-BFGS-B',bounds=[(-2,2)]+[(-2,2)]*k)
    if not r.success:raise ValueError(r.message)
    return r.x


def calibrate(theta,p):
    return softmax(np.log(np.clip(p,1e-12,1))*np.exp(theta[0])+theta[1:],axis=1)


def frame(d,p):
    a=d[['date','kickoff','sport','league','home_team','away_team','y','odds','market']].copy()
    a['probability']=list(p);a['value']=list(p*0)
    return a


def tickets(o,weights):
    a=o.copy();a['v']=np.exp(np.log(a.p)+np.asarray(weights)*np.log(a.price))-1
    ts=select_pairs(a,'value')
    lookup={(int(r.event),int(r.choice)):r for r in a.itertuples()}
    for t in ts:
        legs=[lookup[(e,c)] for e,c in zip(t['events'],t['choices'])]
        t['single_roi']=float(np.mean([r.price*r.hit-1 for r in legs]))
        t['predicted_roi']=t['expected_hit']*t['odds']-1
    return ts


def choose_weight(scores,allowance):
    floor=scores[0.]['pair_accuracy']-allowance
    return max((w for w in GRID if scores[w]['pair_accuracy']>=floor-1e-12),
               key=lambda w:(scores[w]['roi'],-w))


def diagnose(d,p):
    q=np.array(d.market.tolist());odds=np.array(d.odds.tolist());y=d.y.to_numpy(int)
    hit=np.eye(p.shape[1])[y];top=p.argmax(1);idx=np.arange(len(y));bins={}
    groups={}
    for lo,hi in [(0,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,1.01)]:
        groups[f'p_{lo}_{hi}']=(p>=lo)&(p<hi)
    for lo,hi in [(1,1.5),(1.5,2.2),(2.2,4),(4,float('inf'))]:
        groups[f'odds_{lo}_{hi}']=(odds>=lo)&(odds<hi)
    groups['above_market']=p>q;groups['positive_ev']=p*odds>1
    for name,m in groups.items():
        n=int(m.sum())
        bins[name]=dict(n=n,predicted=float(p[m].mean()) if n else None,
            market=float(q[m].mean()) if n else None,actual=float(hit[m].mean()) if n else None,
            roi=float((hit*odds-1)[m].mean()) if n else None)
    return dict(n=len(d),accuracy=float((top==y).mean()),brier=float(brier(p,y).mean()),
        market_brier=float(brier(q,y).mean()),positive_ev_options=int((p*odds>1).sum()),
        top_ev_mean=float((p*odds-1)[idx,top].mean()),bins=bins)


def forecasts(d):
    frames=[];folds=[];diagnostics=[]
    for sport,g in d.groupby('sport'):
        cols=list(dict.fromkeys(['elo_diff',*SPORT_FEATURES[sport]]))
        for year in (2025,2026):
            train=g[g.year<year-1];valid=g[g.year==year-1];test=g[g.year==year]
            assert train.date.max()<valid.date.min()<=valid.date.max()<test.date.min()
            vp=predict(fit(train,cols,True),valid)
            early=valid.date.dt.month.le(6).to_numpy();late=~early
            assert early.any() and late.any()
            theta=calibrate_fit(vp[early],valid.y.to_numpy()[early]);calvp=calibrate(theta,vp[late])
            use_cal=brier(calvp,valid.y.to_numpy()[late]).mean()<brier(vp[late],valid.y.to_numpy()[late]).mean()
            selected_v=calvp if use_cal else vp[late]
            vo=options(frame(valid.loc[late],selected_v))
            scores={w:describe(tickets(vo,w)) for w in GRID}
            if not all(s['tickets'] for s in scores.values()):raise ValueError('Empty validation tickets')
            weights={str(a):choose_weight(scores,a) for a in ALLOWANCES}
            raw=predict(fit(pd.concat([train,valid]),cols,True),test)
            calibrated=calibrate(calibrate_fit(vp,valid.y),raw)
            chosen=calibrated if use_cal else raw
            a=frame(test,chosen);a['raw']=list(raw)
            for tolerance,w in weights.items():a['weight_'+tolerance]=w
            frames.append(a)
            for name,p in [('raw',raw),('calibrated',calibrated),('selected',chosen)]:
                diagnostics.append(dict(sport=sport,year=year,model=name,**diagnose(test,p)))
            folds.append(dict(sport=sport,year=year,calibration_selected=bool(use_cal),weights=weights,validation_scores=scores))
            print(sport,year,'cal',use_cal,'weights',weights,flush=True)
    return pd.concat(frames,ignore_index=True),folds,diagnostics


def summary(ts):
    return dict(**describe(ts),same_legs_single_roi=float(np.mean([t['single_roi'] for t in ts])) if ts else None,
                predicted_pair_hit=float(np.mean([t['expected_hit'] for t in ts])) if ts else None)


def compare(ps):
    common=sorted(set.intersection(*(set(t['date'] for t in ts) for ts in ps.values())))
    maps={p:{t['date']:t for t in ts} for p,ts in ps.items()}
    aligned={p:[m[d] for d in common] for p,m in maps.items()};tests=[]
    for p in ['constraint_0.0','constraint_0.01','constraint_0.02']:
        for field in ('hit','return_'):
            diff=np.array([maps[p][d][field]-maps['probability'][d][field] for d in common]);n=len(diff)
            rng=np.random.default_rng(20261024);samples=[];width=min(7,n)
            for _ in range(20):
                starts=rng.integers(n,size=(500,int(np.ceil(n/width))))
                ids=((starts[:,:,None]+np.arange(width))%n).reshape(500,-1)[:,:n]
                samples.extend(diff[ids].mean(1))
            samples=np.array(samples);delta=float(diff.mean())
            tests.append(dict(policy=p,endpoint=field,delta=delta,ci=np.quantile(samples,[.025,.975]).tolist(),
                p=float((1+(abs(samples-delta)>=abs(delta)-1e-15).sum())/10001)))
    return aligned,tests


def run(path):
    digest=hashlib.sha256(path.read_bytes()).hexdigest();d,quality=load_data(path)
    f,folds,diagnostics=forecasts(d);o=options(f)
    for a in ALLOWANCES:o['weight_'+str(a)]=o.event.map(f['weight_'+str(a)])
    rawf=f.copy();rawf['probability']=rawf['raw'];raw_options=options(rawf)
    result=dict(source_sha256=digest,quality=quality,folds=folds,diagnostics=diagnostics,groups={},tests=[]);ledger={}
    for group in ['pooled','bk','bs','sc','vl']:
        a=o if group=='pooled' else o[o.sport==group]
        b=raw_options if group=='pooled' else raw_options[raw_options.sport==group]
        market=a.copy();market['p']=market.q
        ps=dict(market=tickets(market,0),raw=tickets(b,0),probability=tickets(a,0))
        for tolerance in ALLOWANCES:ps['constraint_'+str(tolerance)]=tickets(a,a['weight_'+str(tolerance)])
        aligned,tests=compare(ps);result['tests'].extend(dict(group=group,**t) for t in tests)
        months=sorted(a.date.dt.to_period('M').astype(str).unique())
        result['groups'][group]=dict(equal_count={p:summary(ts) for p,ts in aligned.items()},
            years={str(y):{p:summary([t for t in ts if t['date'].startswith(str(y))]) for p,ts in aligned.items()} for y in (2025,2026)},
            capped={mode:{p:{str(w):simulate([t for t in ts if mode=='forced' or t['predicted_roi']>0],w,months) for w in (2,3)} for p,ts in ps.items()} for mode in ('forced','ev_veto')})
        ledger[group]=ps;print(group,'complete',flush=True)
    order=sorted(range(len(result['tests'])),key=lambda i:result['tests'][i]['p']);last=0
    for rank,i in enumerate(order):
        last=max(last,min(1,(len(order)-rank)*result['tests'][i]['p']));result['tests'][i]['holm_p']=last
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    return result,ledger


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--games',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();r,ledger=run(args.games);args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'tickets.json').write_text(json.dumps(ledger,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
