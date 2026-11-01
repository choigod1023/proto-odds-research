"""Research-only market-informed goal residual model, Laplace approximation.

Uses frozen out-of-fold DC and inverted market rates; not Egidi's MCMC model.
No target outcomes are accessed by predict().
"""
import hashlib
import json
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, gammaln
from scipy.stats import skellam

SEED = 20261101
DRAWS = 128
LO, HI = np.log(.01), np.log(10.)


def _rates(row):
    values=np.asarray([row['dc_lh'],row['dc_la'],row['market_lh'],row['market_la']],dtype=float)
    if not np.isfinite(values).all() or np.any(values<=0):
        raise ValueError('invalid DC or market rate')
    return values


def _design(rows, teams):
    lookup={t:i for i,t in enumerate(teams)};n=len(teams)
    home=np.array([lookup[r['home_id']] for r in rows],dtype=int)
    away=np.array([lookup[r['away_id']] for r in rows],dtype=int)
    rates=np.array([_rates(r) for r in rows])
    return home,away,rates,n


def objective(beta, home, away, rates, hg, ag, n):
    """Joint score negative log posterior and its analytic gradient."""
    attack=beta[2:2+n]; defence=beta[2+n:2+2*n]
    a=attack-attack.mean(); d=defence-defence.mean(); theta=beta[-1]; w=expit(theta)
    hlog=np.log(rates[:,0])+beta[0]+beta[1]+a[home]+d[away]
    alog=np.log(rates[:,1])+beta[0]+a[away]+d[home]
    th=np.exp(np.clip(hlog,LO,HI));ta=np.exp(np.clip(alog,LO,HI))
    lh=(1-w)*th+w*rates[:,2];la=(1-w)*ta+w*rates[:,3]
    loss=np.sum(lh-hg*np.log(lh)+gammaln(hg+1)+la-ag*np.log(la)+gammaln(ag+1))
    loss += .5*beta[0]**2+2*beta[1]**2+5*(attack@attack+defence@defence)
    loss += 2*np.logaddexp(0,-theta)+2*np.logaddexp(0,theta)
    ph=1-hg/lh;pa=1-ag/la
    dh=ph*(1-w)*th*((hlog>LO)&(hlog<HI));da=pa*(1-w)*ta*((alog>LO)&(alog<HI))
    grad=np.zeros_like(beta);grad[0]=np.sum(dh+da)+beta[0];grad[1]=np.sum(dh)+4*beta[1]
    ga=np.bincount(home,weights=dh,minlength=n)+np.bincount(away,weights=da,minlength=n)
    gd=np.bincount(away,weights=dh,minlength=n)+np.bincount(home,weights=da,minlength=n)
    grad[2:2+n]=ga-ga.mean()+10*attack;grad[2+n:2+2*n]=gd-gd.mean()+10*defence
    grad[-1]=np.sum((ph*(rates[:,2]-th)+pa*(rates[:,3]-ta))*w*(1-w))+4*w-2
    return float(loss),grad


def _hessian(beta,args,step=1e-4):
    columns=[]
    for i in range(len(beta)):
        delta=np.zeros_like(beta);delta[i]=step
        columns.append((objective(beta+delta,*args)[1]-objective(beta-delta,*args)[1])/(2*step))
    h=np.column_stack(columns)
    return (h+h.T)/2


def fit(rows, reference_day):
    reference=date.fromisoformat(reference_day);cutoff=reference-timedelta(days=7)
    if len(rows)<100 or len({r['id'] for r in rows})!=len(rows):
        raise ValueError('need at least 100 unique past fixtures')
    if any(date.fromisoformat(r['day'])>cutoff for r in rows):
        raise ValueError('future or embargoed training fixture')
    if any(type(r[k]) is not int or r[k]<0 for r in rows for k in ('hg','ag')):
        raise ValueError('invalid score')
    if any(not r['home_id'] or not r['away_id'] or r['home_id']==r['away_id'] for r in rows):
        raise ValueError('invalid team identity')
    teams=sorted({r[k] for r in rows for k in ('home_id','away_id')})
    home,away,rates,n=_design(rows,teams)
    hg=np.asarray([r['hg'] for r in rows],float);ag=np.asarray([r['ag'] for r in rows],float)
    args=(home,away,rates,hg,ag,n);bounds=[(-2.,2.)]*(2+2*n)+[(-6.,6.)]
    result=minimize(objective,np.zeros(2*n+3),args=args,jac=True,method='L-BFGS-B',bounds=bounds,
                    options={'maxiter':2000,'ftol':1e-11,'gtol':1e-7})
    if not result.success or not np.isfinite(result.x).all():raise ValueError('MAP optimization failed: '+str(result.message))
    if any(min(x-low,high-x)<=1e-5 for x,(low,high) in zip(result.x,bounds)):
        raise ValueError('MAP near bound')
    h=_hessian(result.x,args);eig=np.linalg.eigvalsh(h)
    if not np.isfinite(eig).all() or eig[0]<=1e-8:raise ValueError('non-positive Laplace Hessian')
    covariance=np.linalg.inv(h);chol=np.linalg.cholesky((covariance+covariance.T)/2)
    rng=np.random.default_rng(SEED);z=rng.standard_normal((DRAWS//2,len(result.x)));z=np.concatenate([z,-z])
    draws=result.x[None,:]+z@chol.T
    return {'teams':teams,'map':result.x.tolist(),'draws':draws.tolist(),'reference_day':reference_day,
            'cutoff_inclusive':cutoff.isoformat(),'training_rows':len(rows),'objective':float(result.fun),
            'hessian_min_eigenvalue':float(eig[0]),'hessian_max_eigenvalue':float(eig[-1]),
            'converged':True,'posterior':'laplace_128_antithetic','seed':SEED}


def _unknown_draws(fixture_id,team):
    token=json.dumps([SEED,str(fixture_id),str(team)],ensure_ascii=False,separators=(',',':')).encode('utf8')
    seed=int.from_bytes(hashlib.sha256(token).digest()[:16],'big')
    z=np.random.default_rng(seed).standard_normal((DRAWS//2,2))/np.sqrt(10.)
    return np.concatenate([z,-z],axis=0)


def predict(state,row):
    rates=_rates(row);teams=state['teams'];n=len(teams);lookup={t:i for i,t in enumerate(teams)}
    home=row['home_id'];away=row['away_id'];fixture_id=row['id']
    if not fixture_id or not home or not away or home==away:raise ValueError('invalid fixture identity')
    draws=np.asarray(state['draws'],float)
    if draws.shape!=(DRAWS,2*n+3) or not np.isfinite(draws).all():raise ValueError('invalid posterior state')
    a=draws[:,2:2+n];d=draws[:,2+n:2+2*n];a=a-a.mean(axis=1,keepdims=True);d=d-d.mean(axis=1,keepdims=True)
    def effects(team):
        if team in lookup:
            j=lookup[team];return a[:,j],d[:,j]
        x=_unknown_draws(fixture_id,team);return x[:,0],x[:,1]
    ah,dh=effects(home);aa,da=effects(away)
    team_h=np.exp(np.clip(np.log(rates[0])+draws[:,0]+draws[:,1]+ah+da,LO,HI))
    team_a=np.exp(np.clip(np.log(rates[1])+draws[:,0]+aa+dh,LO,HI))
    w=expit(draws[:,-1]);lh=(1-w)*team_h+w*rates[2];la=(1-w)*team_a+w*rates[3]
    probs=np.column_stack((skellam.sf(0,lh,la),skellam.pmf(0,lh,la),skellam.cdf(-1,lh,la)))
    p=probs.mean(axis=0)
    if not np.isfinite(p).all() or np.any(p<0) or abs(p.sum()-1)>1e-10:raise ValueError('invalid integrated probabilities')
    return p.tolist(),{'unknown_teams':[t for t in (home,away) if t not in lookup],
                       'posterior_draws':DRAWS,'mean_weight':float(w.mean()),
                       'mean_lambda_home':float(lh.mean()),'mean_lambda_away':float(la.mean())}
