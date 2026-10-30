"""Local, chronological Dixon-Coles / projected-XI experiment versus fixed 9:1."""
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[key] = '1'
from collections import Counter, defaultdict, deque
from datetime import date
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from devig import market_probabilities

HALVES = [None,90,180,365]
SIDES = ['home','draw','away']


def key(r):
    return r['league'],r.get('date',r.get('match_date')),r['home'],r['away']


def projected_features(history):
    """All updates are after every fixture on a calendar day has been featured."""
    batches = defaultdict(list)
    for r in history:
        batches[r['date']].append(r)
    starts, points = Counter(), Counter()
    recent = defaultdict(lambda: deque(maxlen=5))
    result = {}
    for day,batch in sorted(batches.items()):
        for r in batch:
            features, coverage = [], []
            source_dates = []
            projected = []
            for side in ['home','away']:
                t = r['league'],r[side]
                past = recent[t]
                usable = bool(past and (date.fromisoformat(day)-date.fromisoformat(past[-1][0])).days <= 60)
                counts = Counter(p for _,xi in past for p in xi)
                xi = sorted(counts,key=lambda p:(-counts[p],p))[:11]
                usable = usable and len(xi)==11
                if usable:
                    def skill(p): return (points[(t,p)]+1.5)/(starts[(t,p)]+10)
                    total = sum(starts[(t,p)] for p in counts)
                    baseline = sum(skill(p)*starts[(t,p)] for p in counts)/total
                    features.append(10*(sum(skill(p) for p in xi)/11-baseline))
                    source_dates.append(past[-1][0])
                    projected.append(xi)
                else:
                    features.append(0.)
                    projected.append([])
                coverage.append(bool(usable))
            result[key(r)] = dict(values=features, available=coverage, projected=projected,
                latest_source_date=max(source_dates) if source_dates else None)
        for r in batch:
            for side in ['home','away']:
                ps = r.get(side+'_players') or []
                if len(ps)!=11:
                    continue
                t=r['league'],r[side]
                recent[t].append((day,[p['pid'] for p in ps]))
                for p in ps:
                    starts[(t,p['pid'])]+=1
                    points[(t,p['pid'])]+=p['ga']
    return result


def decay_weights(days, cutoff, half):
    ages=np.array([(date.fromisoformat(cutoff)-date.fromisoformat(d)).days for d in days],float)
    if (ages<=0).any():
        raise ValueError('training must be strictly before the forecast month')
    return np.ones(len(days)) if half is None else np.exp2(-ages/half)


def tau_and_derivatives(hg,ag,lh,la,rho):
    tau=np.ones(len(hg));dh=np.zeros(len(hg));da=np.zeros(len(hg));dr=np.zeros(len(hg))
    mask=(hg==0)&(ag==0);v=lh[mask]*la[mask]
    tau[mask]=1-rho*v;dh[mask]=-rho*v;da[mask]=-rho*v;dr[mask]=-v
    mask=(hg==0)&(ag==1)
    tau[mask]=1+rho*lh[mask];dh[mask]=rho*lh[mask];dr[mask]=lh[mask]
    mask=(hg==1)&(ag==0)
    tau[mask]=1+rho*la[mask];da[mask]=rho*la[mask];dr[mask]=la[mask]
    mask=(hg==1)&(ag==1);tau[mask]=1-rho;dr[mask]=-1
    return tau,dh,da,dr


def design(rows,teams,features,players):
    n=len(teams); lookup={t:i for i,t in enumerate(teams)}
    home=np.zeros((len(rows),2+2*n+int(players)));away=home.copy()
    home[:,0]=1;away[:,0]=1;home[:,1]=1
    for i,r in enumerate(rows):
        if r['home'] in lookup:
            j=lookup[r['home']];home[i,2+j]=1;away[i,2+n+j]=-1
        if r['away'] in lookup:
            j=lookup[r['away']];away[i,2+j]=1;home[i,2+n+j]=-1
        if players:
            home[i,-1],away[i,-1]=features[key(r)]['values']
    return home,away


def dc_objective(theta,xh,xa,hg,ag,weights,penalty):
    beta,rho=theta[:-1],theta[-1]
    eh,ea=xh@beta,xa@beta
    lh,la=np.exp(np.clip(eh,-4,math.log(4))),np.exp(np.clip(ea,-4,math.log(4)))
    tau,dh,da,dr=tau_and_derivatives(hg,ag,lh,la,rho)
    if (tau<=0).any(): raise ValueError('invalid DC probability')
    ll=hg*np.log(lh)-lh-gammaln(hg+1)+ag*np.log(la)-la-gammaln(ag+1)+np.log(tau)
    gh=(lh-hg-dh/tau)*((eh>-4)&(eh<math.log(4)))
    ga=(la-ag-da/tau)*((ea>-4)&(ea<math.log(4)))
    grad=xh.T@(weights*gh)+xa.T@(weights*ga)+2*penalty*beta
    loss=-weights@ll+np.sum(penalty*beta**2)+.10*rho*rho
    return loss,np.r_[grad,-weights@(dr/tau)+.20*rho]


def fit(rows,features,cutoff,half,players):
    rows=[r for r in rows if r['date']<cutoff]
    if len(rows)<30:
        raise ValueError('insufficient training')
    teams=sorted({r[s] for r in rows for s in ['home','away']})
    xh,xa=design(rows,teams,features,players);w=decay_weights([r['date'] for r in rows],cutoff,half)
    weights=w/w.sum();hg=np.array([r['hg'] for r in rows]);ag=np.array([r['ag'] for r in rows])
    penalty=np.zeros(xh.shape[1]);penalty[2:]=.02
    if players:penalty[-1]=.10
    def objective(theta):return dc_objective(theta,xh,xa,hg,ag,weights,penalty)
    initial=np.zeros(xh.shape[1]+1);initial[0]=math.log(max(.1,float(np.mean(np.r_[hg,ag]))));initial[1]=.1
    bounds=[(-2,1),(-1,1)]+[(-2,2)]*(xh.shape[1]-2)+[(-.05,.05)]
    fitted=minimize(objective,initial,jac=True,bounds=bounds,method='L-BFGS-B',
                    options=dict(maxiter=500,ftol=1e-11,gtol=1e-7))
    if not fitted.success:raise RuntimeError(fitted.message)
    return dict(teams=teams,beta=fitted.x[:-1],rho=float(fitted.x[-1]),
        n=len(rows),effective_n=float(w.sum()**2/(w@w)),latest_training_date=max(r['date'] for r in rows),
        converged=True,iterations=int(fitted.nit))


def predict(model,rows,features,players):
    xh,xa=design(rows,model['teams'],features,players)
    lh=np.exp(np.clip(xh@model['beta'],-4,math.log(4)))
    la=np.exp(np.clip(xa@model['beta'],-4,math.log(4)))
    probabilities=[]
    for h,a in zip(lh,la):
        goals=np.arange(26);grid=np.outer(poisson.pmf(goals,h),poisson.pmf(goals,a))
        grid[0,0]*=1-h*a*model['rho'];grid[0,1]*=1+h*model['rho']
        grid[1,0]*=1+a*model['rho'];grid[1,1]*=1-model['rho']
        grid/=grid.sum()
        probabilities.append([float(np.tril(grid,-1).sum()),float(np.trace(grid)),float(np.triu(grid,1).sum())])
    return np.array(probabilities)


def score(rows,probabilities,prices=None):
    y=np.array([0 if r['hg']>r['ag'] else 2 if r['hg']<r['ag'] else 1 for r in rows])
    picks=probabilities.argmax(axis=1);good=picks==y
    result=dict(n=len(rows),wins=int(good.sum()),accuracy=float(good.mean()),
        brier=float(((probabilities-np.eye(3)[y])**2).sum(1).mean()),
        log_loss=float(-np.log(np.clip(probabilities[np.arange(len(rows)),y],1e-12,1)).mean()))
    if prices is not None:
        returns=prices[np.arange(len(rows)),picks]*good-1
        result.update(roi=float(returns.mean()),net_per_10000_krw=int(round(returns.sum()*10000)),
                      mean_odds=float(prices[np.arange(len(rows)),picks].mean()))
    return result


def rolling(history,features,evaluation,half,players):
    grouped=defaultdict(list)
    for i,r in enumerate(evaluation):grouped[r['league'],r['date'][:7]].append((i,r))
    out=np.zeros((len(evaluation),3));fits=[]
    for (league,month),batch in sorted(grouped.items()):
        cutoff=month+'-01';train=[r for r in history if r['league']==league and r['date']<cutoff]
        model=fit(train,features,cutoff,half,players)
        p=predict(model,[r for _,r in batch],features,players)
        for (i,_),v in zip(batch,p):out[i]=v
        fits.append(dict(model,beta=model['beta'].tolist(),league=league,cutoff=cutoff,uses_player_proxy=players))
    return out,fits


def experiment(history,targets):
    features=projected_features(history)
    lookup={key(r):r for r in history}
    rows=[lookup[key(dict(t,date=t['match_date']))] for t in targets]
    prices=np.array([t['prices'] for t in targets]);market=np.array([market_probabilities(p.tolist()) for p in prices])
    vote=np.array([[t['vote_counts'].get(s,0)/t['voters'] for s in SIDES] for t in targets])
    reference=np.round(.9*market+.1*vote,4)
    reference=reference/reference.sum(1,keepdims=True)
    assert all(SIDES[int(p.argmax())]==t['picks']['market90_vote10'] for p,t in zip(reference,targets))
    outputs=dict(market=market,market90_tipster10=reference)
    validation=[r for r in history if r['date'].startswith('2025')]
    validation_results={};fit_logs={};chosen={}
    for players in [False,True]:
        family='projected_xi_dc' if players else 'team_dc'
        for half in HALVES:
            name=family+('_uniform' if half is None else f'_h{half}')
            vp,vfits=rolling(history,features,validation,half,players)
            validation_results[name]={l:score([r for r in validation if r['league']==l],vp[[r['league']==l for r in validation]])
                for l in ['K리그1','K리그2']}
            p,logs=rolling(history,features,rows,half,players)
            outputs[name]=p
            outputs['market90_'+name+'10']=.9*market+.1*p
            fit_logs[name]=dict(validation=vfits,evaluation=logs)
        for league in ['K리그1','K리그2']:
            names=[family+('_uniform' if h is None else f'_h{h}') for h in HALVES]
            eligible=[n for n in names if validation_results[n][league]['n']>=20]
            chosen[family+':'+league]=min(eligible,key=lambda n:(validation_results[n][league]['log_loss'],n)) if eligible else None
    for family in ['team_dc','projected_xi_dc']:
        if all(chosen[family+':'+r['league']] for r in rows):
            selected=np.array([outputs[chosen[family+':'+r['league']]][i] for i,r in enumerate(rows)])
            outputs['validation_selected_'+family]=selected
            outputs['market90_validation_selected_'+family+'10']=.9*market+.1*selected
    comparisons={};ledger=[]
    for league in ['K리그1','K리그2','all']:
        mask=np.array([league=='all' or r['league']==league for r in rows])
        comparisons[league]={name:score([r for r,m in zip(rows,mask) if m],p[mask],prices[mask]) for name,p in outputs.items()}
    for i,(r,t) in enumerate(zip(rows,targets)):
        ledger.append(dict(fixture_id=t['fixture_id'],league=r['league'],date=r['date'],home=r['home'],away=r['away'],
            hg=r['hg'],ag=r['ag'],prices=t['prices'],player_feature=features[key(r)],
            models={name:dict(probabilities=p[i].tolist(),pick=SIDES[int(p[i].argmax())]) for name,p in outputs.items()}))
    paired={}
    baseline=reference.argmax(1)
    truth=np.array([0 if r['hg']>r['ag'] else 2 if r['hg']<r['ag'] else 1 for r in rows])
    days=[r['date'] for r in rows]
    for name,p in outputs.items():
        pick=p.argmax(1)
        hit=(pick==truth).astype(int)-(baseline==truth).astype(int)
        net=prices[np.arange(len(rows)),pick]*(pick==truth)-prices[np.arange(len(rows)),baseline]*(baseline==truth)
        weeks=defaultdict(list)
        for i,day in enumerate(days):
            d=date.fromisoformat(day);weeks[d.toordinal()-d.weekday()].append(i)
        groups=[np.array(v,int) for v in weeks.values()]
        rng=np.random.default_rng(20261029);diffs=[]
        for _ in range(3000):
            ix=np.concatenate([groups[j] for j in rng.integers(len(groups),size=len(groups))])
            diffs.append([float(hit[ix].mean()),float(net[ix].mean())])
        low,high=np.quantile(diffs,[.025,.975],axis=0)
        paired[name]=dict(changed_picks=int(np.count_nonzero(pick!=baseline)),
            accuracy_difference=float(hit.mean()),roi_difference=float(net.mean()),
            accuracy_interval=[float(low[0]),float(high[0])],roi_interval=[float(low[1]),float(high[1])],
            observed_weeks=len(groups),method='observed-week bootstrap; retrospective; 3000 draws')
    return dict(validation_year=2025,evaluation_year=2026,validation=validation_results,
        selected_decay_from_validation=chosen,comparisons=comparisons,fit_logs=fit_logs,ledger=ledger,
        paired_vs_fixed_91=paired,
        primary_cohort_n=len(rows),both_projected_lineups_available=sum(all(features[key(r)]['available']) for r in rows),
        monthly_loss_limit_applied=False,equal_flat_stake=10000,production_changes=False,
        live_pregame_lineup_versions_proven=False,independent_holdout=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    args=parser.parse_args()
    def load(name):return [json.loads(x) for x in (args.folder/name).read_text(encoding='utf8').splitlines()]
    result=experiment(load('history.jsonl'),load('targets.jsonl'))
    result['input_hashes_lf']={name:hashlib.sha256((args.folder/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        for name in ['history.jsonl','targets.jsonl','PROTOCOL.md']}
    import scipy
    result['runtime']=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__)
    result['model_code_sha256_lf']=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    (args.folder/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(dict(selected=result['selected_decay_from_validation'],metrics=result['comparisons']),ensure_ascii=False))
