"""Frozen offline preliminary Dixon-Coles model; no operating integration."""
from __future__ import annotations
from datetime import date, timedelta
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln


def parameters(beta, n):
    attack = beta[2:2+n] - beta[2:2+n].mean()
    defence = beta[2+n:2+2*n] - beta[2+n:2+2*n].mean()
    return attack, defence


def objective(beta, home, away, hg, ag, weights, n):
    attack, defence = parameters(beta, n)
    eh = beta[0]+beta[1]+attack[home]+defence[away]
    ea = beta[0]+attack[away]+defence[home]
    lo, hi = np.log(.05), np.log(6.)
    lh, la = np.exp(np.clip(eh, lo, hi)), np.exp(np.clip(ea, lo, hi))
    rho = beta[-1]
    tau = np.ones(len(hg)); dh = np.zeros(len(hg)); da = dh.copy(); dr = dh.copy()
    mask=(hg==0)&(ag==0)
    tau[mask]=1-lh[mask]*la[mask]*rho
    dh[mask]=da[mask]=-lh[mask]*la[mask]*rho; dr[mask]=-lh[mask]*la[mask]
    mask=(hg==0)&(ag==1)
    tau[mask]=1+lh[mask]*rho; dh[mask]=lh[mask]*rho; dr[mask]=lh[mask]
    mask=(hg==1)&(ag==0)
    tau[mask]=1+la[mask]*rho; da[mask]=la[mask]*rho; dr[mask]=la[mask]
    mask=(hg==1)&(ag==1)
    tau[mask]=1-rho; dr[mask]=-1
    if np.any(tau<=0):
        raise ValueError('invalid low-score probability')
    loss=np.sum(weights*(lh-hg*np.log(lh)+gammaln(hg+1)+la-ag*np.log(la)+gammaln(ag+1)-np.log(tau)))
    penalty=np.r_[beta[2:-1],rho]
    loss+=5*np.dot(penalty,penalty)
    gh=weights*(lh-hg-dh/tau)*((eh>lo)&(eh<hi))
    ga=weights*(la-ag-da/tau)*((ea>lo)&(ea<hi))
    grad=np.zeros_like(beta); grad[0]=(gh+ga).sum(); grad[1]=gh.sum()
    gattack=np.bincount(home,weights=gh,minlength=n)+np.bincount(away,weights=ga,minlength=n)
    gdef=np.bincount(away,weights=gh,minlength=n)+np.bincount(home,weights=ga,minlength=n)
    grad[2:2+n]=gattack-gattack.mean()+10*beta[2:2+n]
    grad[2+n:-1]=gdef-gdef.mean()+10*beta[2+n:-1]
    grad[-1]=-np.sum(weights*dr/tau)+10*rho
    return float(loss),grad


def fit(rows, reference_day):
    if len(rows)<200 or len({r['id'] for r in rows})!=len(rows):
        raise ValueError('need 200 unique past official fixtures')
    reference=date.fromisoformat(reference_day)
    cutoff=reference-timedelta(days=7)
    if any(date.fromisoformat(r['day'])>cutoff for r in rows):
        raise ValueError('future or embargoed training fixture')
    teams=sorted({r[k] for r in rows for k in ('home_id','away_id')}); index={t:i for i,t in enumerate(teams)}
    home=np.array([index[r['home_id']] for r in rows]); away=np.array([index[r['away_id']] for r in rows])
    hg=np.array([r['hg'] for r in rows]); ag=np.array([r['ag'] for r in rows])
    if any(type(r[k]) is not int or r[k]<0 for r in rows for k in ('hg','ag')):
        raise ValueError('invalid official goals')
    weights=np.array([2**(-(reference-date.fromisoformat(r['day'])).days/180) for r in rows])
    n=len(teams); initial=np.zeros(2*n+3);initial[0]=np.log(max(.05,float((hg+ag).mean()/2)))
    bounds=[(-2.,2.),(-1.,1.)]+[(-2.,2.)]*(2*n)+[(-.15,.025)]
    result=minimize(objective,initial,args=(home,away,hg,ag,weights,n),jac=True,method='L-BFGS-B',
                    bounds=bounds,options={'maxiter':2000,'ftol':1e-11,'gtol':1e-7})
    if not result.success:
        raise ValueError('optimizer failed: '+str(result.message))
    attack,defence=parameters(result.x,n)
    return dict(teams=teams,attack=attack.tolist(),defence=defence.tolist(),intercept=float(result.x[0]),
                home=float(result.x[1]),rho=float(result.x[-1]),reference_day=reference_day,
                cutoff_inclusive=cutoff.isoformat(),training_rows=len(rows),objective=float(result.fun),
                beta=result.x.tolist(),converged=True)


def joint(lh,la,rho):
    if not (.05<=lh<=6 and .05<=la<=6 and -.15<=rho<=.025):
        raise ValueError('outside frozen rate/rho bounds')
    g=np.arange(31)
    hp=np.exp(-lh+g*np.log(lh)-gammaln(g+1));ap=np.exp(-la+g*np.log(la)-gammaln(g+1))
    matrix=hp[:,None]*ap[None,:]
    matrix[0,0]*=1-lh*la*rho;matrix[0,1]*=1+lh*rho
    matrix[1,0]*=1+la*rho;matrix[1,1]*=1-rho
    if not np.isfinite(matrix).all() or np.min(matrix)<0 or abs(matrix.sum()-1)>1e-10:
        raise ValueError('invalid probability mass or large tail')
    return matrix/matrix.sum()


def predict(state, home, away):
    index={name:i for i,name in enumerate(state['teams'])}
    def effect(field,team):
        return state[field][index[team]] if team in index else 0.
    lh=float(np.clip(np.exp(state['intercept']+state['home']+effect('attack',home)+effect('defence',away)),.05,6.))
    la=float(np.clip(np.exp(state['intercept']+effect('attack',away)+effect('defence',home)),.05,6.))
    m=joint(lh,la,state['rho'])
    return [float(np.tril(m,-1).sum()),float(np.trace(m)),float(np.triu(m,1).sum())],dict(lambda_home=lh,lambda_away=la,
        unknown_teams=[t for t in (home,away) if t not in index])
