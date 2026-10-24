"""Offline causal team-state experiment; exploratory, never promotes policies."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sport_hybrid_validation import load_data, fit, predict, brier, paper
from model_v2 import SPORT_FEATURES

NAMES=('market','legacy','calibration','league','slow','fast','uncertain')


def state_features(data, process):
    """Diagonal Gaussian approximation to market-relative team performance.

    Same-date forecasts are frozen together. Missing history starts at mean zero;
    uncertainty increases between appearances. This is not a player/score model.
    """
    d=data.sort_values(['date','league','home_team','away_team']).copy()
    history={}; values={}
    def prior(key,day):
        mean,var,last=history.get(key,(0.,.1,day))
        return mean,var+process*min(60,max(0,(day-last).days))
    for day,group in d.groupby('date',sort=True):
        updates=defaultdict(list)
        for row in group.itertuples():
            h=(row.sport,row.league,row.home_team); a=(row.sport,row.league,row.away_team)
            hm,hv=prior(h,day); am,av=prior(a,day)
            values[row.Index]=(hm-am,hv+av)
            q=np.array(row.market)
            expected=q[0]+(.5*q[1] if len(q)==3 else 0.)
            actual=1. if row.y==0 else (.5 if len(q)==3 and row.y==1 else 0.)
            innovation=actual-expected-(hm-am)
            denom=.25+hv+av
            updates[h].append((hm+hv/denom*innovation,hv-hv*hv/denom))
            updates[a].append((am-av/denom*innovation,av-av*av/denom))
        # Doubleheaders get symmetric averaging, never same-date label feedback.
        for key,items in updates.items():
            history[key]=(*np.mean(items,axis=0),day)
    return pd.DataFrame.from_dict(values,orient='index',columns=['state','variance']).reindex(data.index)


def candidates(train,test,cols):
    ps={'market':np.array(test.market.tolist())}
    global_cal=fit(train,[],True)
    ps['calibration']=predict(global_cal,test)
    ps['legacy']=predict(fit(train,cols,True),test)
    ps['league']=ps['calibration'].copy()
    for league,g in train.groupby('league'):
        mask=test.league.eq(league).to_numpy()
        if len(g)>=200 and mask.any():
            ps['league'][mask]=.5*ps['league'][mask]+.5*predict(fit(g,[],True),test.loc[mask])
    for name in ('slow','fast'):
        ps[name]=predict(fit(train,cols+[name+'_state'],True),test)
    w=1/(1+test.fast_variance.to_numpy()/.1)
    ps['uncertain']=ps['market']+w[:,None]*(ps['fast']-ps['market'])
    return ps


def choose(ps,y):
    return min(NAMES,key=lambda n:(-float((ps[n].argmax(1)==y).mean()),float(brier(ps[n],y).mean())))


def top_two(data,p):
    """Fixed market choices, same per-date capacity, candidate ranks only."""
    a=data[['date','league','home_team','away_team']].copy()
    pick=np.array(data.market.tolist()).argmax(1)
    a['confidence']=p[np.arange(len(data)),pick]
    ordered=a.sort_values(['date','confidence','league','home_team','away_team'],ascending=[True,False,True,True,True])
    return ordered.groupby('date',sort=True).head(2).index.to_numpy()


def block_inference(sums,counts,repeats=20000,seed=20261024):
    """Joint circular 7-observed-date blocks; bounded memory, centered null."""
    sums=np.asarray(sums,float); counts=np.asarray(counts,float)
    observed=sums.sum(0)/counts.sum(0)
    rng=np.random.default_rng(seed); draws=[]; n=len(sums); width=min(7,n)
    for start in range(0,repeats,500):
        starts=rng.integers(0,n,size=(min(500,repeats-start),int(np.ceil(n/width))))
        idx=((starts[:,:,None]+np.arange(width))%n).reshape(len(starts),-1)[:,:n]
        draws.append(sums[idx].sum(1)/counts[idx].sum(1))
    samples=np.concatenate(draws)
    ci=np.quantile(samples,[.025,.975],axis=0).T
    p=(1+(np.abs(samples-observed)>=np.abs(observed)-1e-15).sum(0))/(repeats+1)
    return observed,ci,p


def holm(ps):
    ps=np.asarray(ps,float); order=np.argsort(ps); adjusted=np.empty(len(ps)); prev=0.
    for i,j in enumerate(order):
        prev=max(prev,min(1.,(len(ps)-i)*ps[j])); adjusted[j]=prev
    return adjusted


def evaluate(data,ps):
    data=data.reset_index(drop=True); y=data.y.to_numpy(int)
    odds=np.array(data.odds.tolist()); q=ps['market']; base=q.argmax(1)==y
    market_pick=q.argmax(1); base_idx=top_two(data,q)
    dates=pd.Index(sorted(data.date.unique())); codes=dates.get_indexer(data.date)
    nday=len(dates); counts=np.bincount(codes,minlength=nday)
    rank_counts=np.bincount(codes[base_idx],minlength=nday)
    base_rank=np.bincount(codes[base_idx],weights=base[base_idx].astype(float),minlength=nday)
    matrix=[]; denominators=[]; keys=[]; results={}
    for name,p in ps.items():
        pick=p.argmax(1); hit=pick==y; idx=top_two(data,p)
        if len(idx)!=len(base_idx): raise ValueError('Unequal selection budget')
        ranks=np.bincount(codes[idx],weights=base[idx].astype(float),minlength=nday)
        if not np.array_equal(np.bincount(codes[idx],minlength=nday),rank_counts): raise ValueError('Unequal daily budget')
        years={}
        for yr,ids in data.groupby('year').indices.items():
            chosen=idx[np.isin(idx,ids)]; chosen_base=base_idx[np.isin(base_idx,ids)]
            years[str(yr)]=dict(n=len(ids),accuracy=float(hit[ids].mean()),delta=float((hit[ids].astype(float)-base[ids]).mean()),
                               rank_delta=float(base[chosen].mean()-base[chosen_base].mean()))
        prices=odds[idx,market_pick[idx]]
        results[name]=dict(n=len(y),accuracy=float(hit.mean()),brier=float(brier(p,y).mean()),
            roi=float((odds[np.arange(len(y)),pick]*hit-1).mean()),
            ranked=dict(n=len(idx),accuracy=float(base[idx].mean()),average_odds=float(prices.mean()),
                        odds_bins={label:int(mask.sum()) for label,mask in [('under1.5',prices<1.5),('1.5to2.2',(prices>=1.5)&(prices<2.2)),('2.2plus',prices>=2.2)]}),
            years=years,leagues={str(l):dict(n=len(ids),delta=float((hit[ids].astype(float)-base[ids]).mean())) for l,ids in data.groupby('league').indices.items()},
            paper={str(w):paper(data,p,w) for w in (2,3)})
        if name!='market':
            matrix.extend([np.bincount(codes,weights=hit.astype(float)-base,minlength=nday),ranks-base_rank])
            denominators.extend([counts,rank_counts]); keys.extend([(name,'all'),(name,'ranked')])
    delta,ci,pv=block_inference(np.array(matrix).T,np.array(denominators).T)
    tests=[]
    for (name,endpoint),v,c,p in zip(keys,delta,ci,pv):
        tests.append(dict(model=name,endpoint=endpoint,delta=float(v),ci=c.tolist(),p=float(p)))
    return results,tests


def run(path):
    fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
    data,quality=load_data(path)
    for name,process in [('slow',.0002),('fast',.002)]:
        f=state_features(data,process)
        for c in f: data[name+'_'+c]=f[c]
    report=dict(source_sha256=fingerprint,quality=quality,sports={},comparisons=[])
    for sport,d in data.groupby('sport'):
        cols=list(dict.fromkeys(['elo_diff',*SPORT_FEATURES[sport]]))
        frames=[]; stored=defaultdict(list); folds=[]
        for year in (2025,2026):
            train=d[d.year<year-1]; valid=d[d.year==year-1]; test=d[d.year==year]
            if any(x.empty for x in (train,valid,test)) or not train.date.max()<valid.date.min()<=valid.date.max()<test.date.min():
                raise ValueError('Missing or overlapping fold')
            selected=choose(candidates(train,valid,cols),valid.y.to_numpy())
            ps=candidates(pd.concat([train,valid]),test,cols); ps['selected']=ps[selected]
            for name,p in ps.items():
                if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(1),1): raise ValueError('Invalid probabilities')
                stored[name].append(p)
            frames.append(test)
            folds.append(dict(year=year,train=len(train),validation=len(valid),test=len(test),selected=selected))
        out=pd.concat(frames,ignore_index=True)
        metrics,tests=evaluate(out,{n:np.concatenate(v) for n,v in stored.items()})
        report['sports'][sport]=dict(folds=folds,models=metrics)
        for t in tests: report['comparisons'].append(dict(sport=sport,**t))
        print(sport,{n:round(v['accuracy']*100,3) for n,v in metrics.items()},flush=True)
    adjusted=holm([t['p'] for t in report['comparisons']])
    for t,p in zip(report['comparisons'],adjusted):
        t['holm_p']=float(p)
        years=report['sports'][t['sport']]['models'][t['model']]['years']
        field='delta' if t['endpoint']=='all' else 'rank_delta'
        t['both_years_positive']=all(v[field]>0 for v in years.values())
        t['exploratory_pass']=bool(t['delta']>0 and p<.05 and t['both_years_positive'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=fingerprint: raise ValueError('Source changed')
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--games',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(); result=run(args.games)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
