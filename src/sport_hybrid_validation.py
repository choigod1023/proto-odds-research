"""Offline primary-market hybrid ablation; never promotes operating policies."""
from __future__ import annotations

import os
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax, logsumexp

from accuracy_formula_lab import load_unique_market_rows, fit_design, apply_design, date_block_interval
from devig import market_probabilities
from features import build_features
from matches import load_matches
from model_v2 import SPORT_FEATURES, PI_PARAMS, PI_GAMMA
import pi_ratings

KEY = ['kickoff', 'sport', 'league', 'home_team', 'away_team']
NAMES = ('market', 'calibration', 'elo', 'legacy_features', 'market_residual', 'blend')


def load_data(path):
    records, quality = load_unique_market_rows(path)
    primary = []
    for r in records:
        key = r['key']
        sport = r['sport']
        if (sport == 'sc' and key[5] == '승무패' and r['n_way'] == 3 or
                sport in ('bs', 'bk', 'vl') and key[5] == '승패' and r['n_way'] == 2):
            primary.append(dict(zip(KEY, key[:5]), y=r['winner'], odds=list(r['odds'])))
    prices = pd.DataFrame(primary)
    # Reject any physical event still having multiple primary offers.
    prices = prices.loc[~prices.duplicated(KEY, keep=False)]
    matches = load_matches(path=path)
    f = build_features(matches)
    old = pi_ratings.LAMBDA, pi_ratings.DAMP, pi_ratings.GAMMA
    try:
        pi_ratings.LAMBDA = {s: p[0] for s,p in PI_PARAMS.items()}
        pi_ratings.DAMP = {s: p[1] for s,p in PI_PARAMS.items()}
        pi_ratings.GAMMA = PI_GAMMA
        pi = pi_ratings.run_pi(matches)
    finally:
        pi_ratings.LAMBDA, pi_ratings.DAMP, pi_ratings.GAMMA = old
    f = f.merge(pi[KEY + ['pi_diff']], on=KEY, validate='one_to_one')
    d = f.merge(prices, on=KEY, validate='one_to_one')
    expected = np.where(d.outcome.eq(1), 0, np.where(d.outcome.eq(.5), 1, np.where(d.sport.eq('sc'),2,1)))
    consistent = d.y.eq(expected) & ~(d.outcome.eq(.5) & ~d.sport.eq('sc'))
    quality['score_conflicts'] = int((~consistent).sum())
    d = d[consistent].sort_values(KEY).reset_index(drop=True)
    d['market'] = d.odds.map(market_probabilities)
    quality['joined_primary_events'] = len(d)
    return d, quality


def fit(train, cols, offset=False):
    x, state = fit_design(train, cols)
    p = np.array(train.market.tolist())
    k = p.shape[1]
    z = np.log(p) if offset else np.zeros_like(p)
    y = train.y.to_numpy(int)
    target = np.eye(k)[y]
    def loss_grad(flat):
        b = flat.reshape(x.shape[1], k)
        logits = z + x @ b
        loss = (logsumexp(logits, axis=1)-logits[np.arange(len(y)),y]).mean()
        penalty = b.copy(); penalty[0] = 0
        grad = x.T @ (softmax(logits, axis=1)-target)/len(y) + .02*penalty
        return loss + .01*np.sum(penalty**2), grad.ravel()
    result = minimize(loss_grad, np.zeros(x.shape[1]*k), jac=True, method='L-BFGS-B', options={'maxiter':500})
    if not result.success:
        raise RuntimeError(result.message)
    return state, result.x.reshape(x.shape[1], k), offset


def predict(model, data):
    state,b,offset = model
    z = np.log(np.array(data.market.tolist())) if offset else 0
    return softmax(z + apply_design(data, state) @ b, axis=1)


def brier(p, y):
    return ((p-np.eye(p.shape[1])[np.asarray(y,int)])**2).sum(axis=1)


def choose_blend(market, model, y):
    grid = (1., .75, .5, .25, 0.)
    return min(grid, key=lambda w: brier(w*market+(1-w)*model, y).mean())


def paper(d, p, weekly):
    """One ticket/day; next-day settlement proxy, bet-month accounting."""
    a = d.copy(); odds=np.array(d.odds.tolist())
    ev=p*odds-1; pick=ev.argmax(1)
    a['edge']=ev[np.arange(len(a)),pick]
    a['price']=odds[np.arange(len(a)),pick]
    a['hit']=pick==a.y.to_numpy()
    months, weeks, profits = {}, defaultdict(int), []
    for day, rows in a.groupby('date',sort=True):
        month=str(day.to_period('M')); week=tuple(day.isocalendar()[:2])
        months.setdefault(month,0.)
        if months[month]-10000 < -50000 or weeks[week]>=weekly:
            continue
        pair=rows[rows.edge>0].sort_values(['edge','league','home_team','away_team'],ascending=[False,True,True,True]).head(2)
        if len(pair)<2: continue
        profit=10000*(float(pair.price.prod())*bool(pair.hit.all())-1)
        months[month]+=profit; weeks[week]+=1; profits.append(profit)
    return dict(tickets=len(profits),net=round(sum(profits)),roi=sum(profits)/(10000*len(profits)) if profits else None,
                monthly_net={m:round(v) for m,v in months.items()},worst_month=round(min(months.values(),default=0)))


def score(d,p):
    y=d.y.to_numpy(int); pick=p.argmax(1); q=np.array(d.market.tolist())
    losses=brier(p,y); delta=losses-brier(q,y)
    accuracy_delta=(pick==y).astype(float)-(q.argmax(1)==y).astype(float)
    intervals=date_block_interval(pd.DataFrame({'date':d.date.to_numpy(),'delta':delta,'accuracy_delta':accuracy_delta}),
                                  ['delta','accuracy_delta'],repeats=2000,seed=20261024,block_dates=7)
    odds=np.array(d.odds.tolist()); pnl=odds[np.arange(len(d)),pick]*(pick==y)-1
    groups={}
    for league, idx in d.groupby('league').indices.items():
        if len(idx)>=50:
            groups[league]=dict(n=len(idx),accuracy=float((pick[idx]==y[idx]).mean()),brier_delta=float(delta[idx].mean()))
    bins=[]
    for lo,hi in ((0,.5),(.5,.6),(.6,.7),(.7,.8),(.8,1.01)):
        mask=(p.max(1)>=lo)&(p.max(1)<hi)
        if mask.any(): bins.append(dict(lower=lo,n=int(mask.sum()),predicted=float(p.max(1)[mask].mean()),actual=float((pick[mask]==y[mask]).mean())))
    return dict(n=len(d),accuracy=float((pick==y).mean()),brier=float(losses.mean()),
                log_loss=float(-np.log(np.clip(p[np.arange(len(d)),y],1e-12,1)).mean()),
                unit_top_pick_roi=float(pnl.mean()),paired_brier_delta_ci=intervals['delta'],
                paired_accuracy_delta_ci=intervals['accuracy_delta'],leagues=groups,calibration=bins,
                paper={str(w):paper(d,p,w) for w in (2,3)})


def run(path):
    d,quality=load_data(path)
    report={'source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'quality':quality,
            'scope':'primary markets only; retrospective, no promotion', 'sports':{}}
    for sport, data in d.groupby('sport'):
        outputs=[]; predictions=defaultdict(list); folds=[]
        cols=list(dict.fromkeys(['elo_diff',*SPORT_FEATURES[sport]]))
        for year in (2025,2026):
            train=data[data.year<year-1]; validation=data[data.year==year-1]; test=data[data.year==year]
            if len(train)<100 or len(validation)<50 or len(test)<50: continue
            if train.date.max()>=validation.date.min() or validation.date.max()>=test.date.min():
                raise ValueError('Temporal overlap')
            specs={'calibration':([],True),'elo':(['elo_diff'],False),
                   'legacy_features':(cols,False),'market_residual':(cols,True)}
            vp={'market':np.array(validation.market.tolist())}
            for name,(c,off) in specs.items(): vp[name]=predict(fit(train,c,off),validation)
            w=choose_blend(vp['market'],vp['legacy_features'],validation.y)
            vp['blend']=w*vp['market']+(1-w)*vp['legacy_features']
            selected=min(NAMES,key=lambda n:brier(vp[n],validation.y).mean())
            tp={'market':np.array(test.market.tolist())}
            refit=pd.concat([train,validation])
            for name,(c,off) in specs.items(): tp[name]=predict(fit(refit,c,off),test)
            tp['blend']=w*tp['market']+(1-w)*tp['legacy_features']
            tp['selected']=tp[selected]
            folds.append(dict(year=year,train=len(train),validation=len(validation),test=len(test),market_weight=w,selected=selected,
                              test_accuracy={n:float((p.argmax(1)==test.y.to_numpy()).mean()) for n,p in tp.items()}))
            outputs.append(test)
            for n,p in tp.items(): predictions[n].append(p)
        if not outputs: continue
        oos=pd.concat(outputs).reset_index(drop=True)
        report['sports'][sport]={'folds':folds,'start':str(oos.date.min().date()),'end':str(oos.date.max().date()),
                                'models':{n:score(oos,np.concatenate(p)) for n,p in predictions.items()}}
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--games',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(); r=run(args.games); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({s:{n:round(v['accuracy'],4) for n,v in x['models'].items()} for s,x in r['sports'].items()}))
