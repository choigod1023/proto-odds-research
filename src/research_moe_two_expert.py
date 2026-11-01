"""Frozen offline market/DC gate. No tipster expert or operating integration."""
from datetime import date, timedelta
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


def features(row):
    q=np.asarray(row['market_p'],dtype=float);d=np.asarray(row['dc_p'],dtype=float)
    for p in (q,d):
        if p.shape!=(3,) or not np.isfinite(p).all() or min(p)<=0 or abs(p.sum()-1)>1e-9:
            raise ValueError('need complete finite positive 3-way probabilities')
    if type(row['unknown_dc_team']) is not bool:raise ValueError('unknown team flag must be bool')
    return np.array([max(q)-1/3,q[1],sum(abs(q-d)),float(row['unknown_dc_team'])])


def objective(beta,x,market,dc,y):
    gate=expit(x@beta)
    chosen_market=market[np.arange(len(y)),y];chosen_dc=dc[np.arange(len(y)),y]
    chosen=gate*chosen_market+(1-gate)*chosen_dc
    loss=-np.log(chosen).sum()+5*np.dot(beta[1:],beta[1:])
    grad=x.T@(-(chosen_market-chosen_dc)/chosen*gate*(1-gate))
    grad[1:]+=10*beta[1:]
    return float(loss),grad


def fit(rows,reference_day):
    cutoff=(date.fromisoformat(reference_day)-timedelta(days=7)).isoformat()
    if len(rows)<100 or len({r['id'] for r in rows})!=len(rows):raise ValueError('need 100 unique past rows')
    if any(date.fromisoformat(r['day']).isoformat()>cutoff for r in rows):raise ValueError('future or embargoed row')
    if any(type(r['y']) is not int or r['y'] not in (0,1,2) for r in rows):raise ValueError('invalid categorical outcome')
    raw=np.array([features(r) for r in rows]);mean=raw.mean(0);scale=raw.std(0);scale[scale==0]=1
    x=np.column_stack([np.ones(len(rows)),(raw-mean)/scale])
    q=np.array([r['market_p'] for r in rows]);d=np.array([r['dc_p'] for r in rows]);y=np.array([r['y'] for r in rows])
    opt=minimize(objective,np.zeros(5),args=(x,q,d,y),jac=True,method='L-BFGS-B',
                 bounds=[(-8,8)]+[(-5,5)]*4,options={'maxiter':2000,'ftol':1e-11,'gtol':1e-7})
    if not opt.success:raise ValueError('gate optimizer failed: '+str(opt.message))
    return dict(beta=opt.x.tolist(),mean=mean.tolist(),scale=scale.tolist(),reference_day=reference_day,
                cutoff_inclusive=cutoff,training_rows=len(rows),objective=float(opt.fun),converged=True,
                experts=['market','dc'],tipster_expert=False,
                gradient_max=float(max(abs(opt.jac))),boundary_contact=bool(abs(opt.x[0])>=8-1e-5 or max(abs(opt.x[1:]))>=5-1e-5))


def predict(state,row):
    raw=features(row);x=np.r_[1.,(raw-np.array(state['mean']))/np.array(state['scale'])]
    g=float(expit(x@np.array(state['beta'])))
    p=g*np.array(row['market_p'])+(1-g)*np.array(row['dc_p'])
    return p.tolist(),dict(market_weight=g,dc_weight=1-g,tipster_expert=False)
