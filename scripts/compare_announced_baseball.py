"""Actual pregame MLB inputs vs same-fixture market/tipster baseline; local only."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path
from itertools import product
import argparse
import json
import math
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from collect_baseball_pregame_evidence import read


def num(value,default=0.):
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except (ValueError,TypeError):return default


def player_rates(player):
    p=player.get('pitching',{});b=player.get('batting',{})
    outs=num(p.get('outs'))
    era=(27*num(p.get('earnedRuns'))+150*4.3)/(outs+150)
    kbb=(num(p.get('strikeOuts'))-num(p.get('baseOnBalls'))+100*.14)/(num(p.get('battersFaced'))+100)
    pa=num(b.get('atBats'))+num(b.get('baseOnBalls'))+num(b.get('hitByPitch'))+num(b.get('sacFlies'))
    obp=(num(b.get('hits'))+num(b.get('baseOnBalls'))+num(b.get('hitByPitch'))+150*.32)/(pa+150)
    slg=(num(b.get('totalBases'))+150*.41)/(num(b.get('atBats'))+150)
    return era,kbb,obp,slg


def features(row,savant):
    year=int(row['start_at'][:4])-1
    sides=[];coverage={}
    for side in ['home','away']:
        s=row['sides'][side];era,kbb,_,_=player_rates(s['starter'])
        rates=[player_rates(p) for p in s['lineup']]
        obp=float(np.mean([x[2] for x in rates]));slg=float(np.mean([x[3] for x in rates]))
        def expected(player,kind,field,prior,shrink):
            r=savant[(year,kind)].get(player['id'],{})
            pa=num(r.get('pa'));value=num(r.get(field),prior)
            return (pa*value+shrink*prior)/(pa+shrink),bool(r)
        px,pk=expected(s['starter'],'pitcher','est_woba',.32,100)
        xe,_=expected(s['starter'],'pitcher','xera',4.3,100)
        bats=[expected(p,'batter','est_woba',.32,150) for p in s['lineup']]
        coverage[side]=dict(starter_statcast=pk,lineup_statcast=sum(x[1] for x in bats),
            starter_bf=num(s['starter'].get('pitching',{}).get('battersFaced')))
        sides.append((era,kbb,obp,slg,px,xe,float(np.mean([x[0] for x in bats]))))
    h,a=sides
    return np.array([math.log(a[0]/h[0]),h[1]-a[1],h[2]-a[2],h[3]-a[3],a[4]-h[4],a[5]-h[5],h[6]-a[6]]),coverage


def objective(beta,x,y,ridge):
    z=x@beta
    loss=np.mean(np.logaddexp(0,z)-y*z)+ridge*np.dot(beta[1:],beta[1:])/2
    grad=x.T@(expit(z)-y)/len(y)+np.r_[0.,ridge*beta[1:]]
    return float(loss),grad


def fit(x,y,ridge):
    center=x.mean(axis=0);scale=x.std(axis=0);scale[scale<1e-8]=1
    design=np.c_[np.ones(len(x)),np.clip((x-center)/scale,-6,6)]
    result=minimize(objective,np.zeros(design.shape[1]),args=(design,y,ridge),method='L-BFGS-B',jac=True,
        options=dict(maxiter=250,ftol=1e-12,gtol=1e-8))
    if not result.success:raise ValueError(str(result.message))
    return dict(center=center.tolist(),scale=scale.tolist(),beta=result.x.tolist(),ridge=ridge)


def predict(model,x):
    design=np.c_[np.ones(len(x)),np.clip((x-np.array(model['center']))/np.array(model['scale']),-6,6)]
    return expit(design@np.array(model['beta']))


def stats(rows,probabilities):
    wins=losses=0;profits=[];monthly=defaultdict(float)
    labels=[];probs=[]
    for r,p in zip(rows,probabilities):
        t=r.get('target');hs,aws=(t['hg'],t['ag']) if t else (r['home_score'],r['away_score'])
        draw=hs==aws;home=p>=.5;correct=(hs>aws)==home
        wins+=int(correct and not draw);losses+=int(not correct and not draw)
        if not draw:labels.append(int(hs>aws));probs.append(p)
        if t:
            price=t['prices'][t['order'].index('home' if home else 'away')]
            net=0. if draw else price-1 if correct else -1.
            profits.append(net);monthly[t['date'][:7]]+=net
    p=np.array(probs);y=np.array(labels)
    out=dict(n=len(rows),wins=wins,losses=losses,pushes=len(rows)-wins-losses,accuracy=wins/(wins+losses),
        brier=float(np.mean((p-y)**2)))
    if profits:out.update(roi=float(np.mean(profits)),net_krw=round(sum(profits)*10000),monthly_units=dict(monthly))
    return out


def paired_interval(rows,p,q):
    groups=defaultdict(lambda:np.zeros(3))
    for r,a,b in zip(rows,p,q):
        t=r['target'];d=date.fromisoformat(t['date']);k=(d-timedelta(days=d.weekday())).isoformat()
        win='home' if t['hg']>t['ag'] else 'away'
        picks=['home' if v>=.5 else 'away' for v in [a,b]]
        hit=[int(v==win) for v in picks]
        nets=[t['prices'][t['order'].index(v)]-1 if v==win else -1 for v in picks]
        groups[k]+=[hit[0]-hit[1],nets[0]-nets[1],1]
    values=np.array(list(groups.values()));rng=np.random.default_rng(20261029)
    sums=values[rng.integers(len(values),size=(3000,len(values)))].sum(axis=1)
    return dict(observed_weeks=len(values),accuracy_delta95=np.quantile(sums[:,0]/sums[:,2],[.025,.975]).tolist(),
        roi_delta95=np.quantile(sums[:,1]/sums[:,2],[.025,.975]).tolist())


def run(folder):
    all_rows=read(folder/'mlb-snapshots.jsonl');rows=sorted([r for r in all_rows if r['accepted']],key=lambda r:(r['start_at'],r['gamePk']))
    savant={}
    for year,kind in product([2023,2024,2025],['batter','pitcher']):
        table=json.loads((folder/f'savant-{year}-{kind}.json').read_text(encoding='utf8'))
        assert all(int(r['year'])==year for r in table['rows'])
        savant[year,kind]={int(r['player_id']):r for r in table['rows']}
    train=[r for r in rows if r['role']=='train'];val=[r for r in rows if r['role']=='validation'];evaluation=[r for r in rows if r['role']=='evaluation']
    assert max(r['start_at'] for r in train)<min(r['start_at'] for r in val)
    assert max(r['start_at'] for r in val)<min(r['start_at'] for r in evaluation)
    for r in evaluation:
        assert (r['home_score'],r['away_score'])==(r['target']['hg'],r['target']['ag'])
    matrix={r['gamePk']:features(r,savant)[0] for r in rows}
    y=lambda rs:np.array([int(r['home_score']>r['away_score']) for r in rs])
    market=np.array([market_probabilities(r['target']['prices'])[r['target']['order'].index('home')] for r in evaluation])
    votes=np.array([r['target']['vote_counts'].get('home',0)/r['target']['voters'] for r in evaluation]);baseline=.9*market+.1*votes
    candidates={};probs={};models={}
    for bundle,size in [('starter',2),('lineup',4),('statcast',7)]:
        x=lambda rs:np.array([matrix[r['gamePk']][:size] for r in rs])
        for ridge in [.1,1.,10.]:
            name=f'{bundle}-ridge{ridge:g}';m=fit(x(train),y(train),ridge)
            candidates[name]=dict(validation=stats(val,predict(m,x(val))))
            fitted=dict(before2025=m,before2026=fit(x(train+val),y(train+val),ridge),size=size)
            ps=np.array([predict(fitted['before2025' if r['start_at'][:4]=='2025' else 'before2026'],x([r]))[0] for r in evaluation])
            candidates[name]['evaluation']=stats(evaluation,ps);models[name]=fitted;probs[name]=ps
    chosen=min(candidates,key=lambda n:(-candidates[n]['validation']['accuracy'],candidates[n]['validation']['brier'],n))
    policies={'market':market,'market90_vote10':baseline,'tipster_vote':votes,'selected_pure':probs[chosen],
        'market90_new10':.9*market+.1*probs[chosen],'existing91_90_new10':.9*baseline+.1*probs[chosen],
        'home_only':np.ones(len(evaluation)),'away_only':np.zeros(len(evaluation))}
    report=dict(train_n=len(train),validation_n=len(val),evaluation_n=len(evaluation),selected=chosen,candidates=candidates,
        policies={n:stats(evaluation,p) for n,p in policies.items()},models=models,
        selected_vs91=paired_interval(evaluation,probs[chosen],baseline),
        mixture_vs91=paired_interval(evaluation,policies['existing91_90_new10'],baseline),
        coverage=[dict(gamePk=r['gamePk'],**features(r,savant)[1]) for r in evaluation],
        diagnostics=dict(selected_probability_min=float(probs[chosen].min()),selected_probability_max=float(probs[chosen].max()),
            selected_home_picks=int(sum(probs[chosen]>=.5)),training_home_win_rate=float(y(train).mean()),
            validation_home_win_rate=float(y(val).mean()),evaluation_home_win_rate=float(y(evaluation).mean())))
    (folder/'mlb-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    ledger=[]
    for i,r in enumerate(evaluation):ledger.append(dict(gamePk=r['gamePk'],target=r['target'],
        snapshot_at=r['snapshot_at'],cutoff_at=r['cutoff_at'],start_at=r['start_at'],source_url=r['url'],
        features=matrix[r['gamePk']].tolist(),probabilities={n:float(p[i]) for n,p in policies.items()},
        candidate_probabilities={n:float(p[i]) for n,p in probs.items()}))
    (folder/'mlb-evaluation.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ledger),encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k in ['train_n','validation_n','evaluation_n','selected','policies','selected_vs91']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);run(p.parse_args().folder)
