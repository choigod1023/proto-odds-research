"""Point-estimated contextual simplex stack. No outcome enters the feature map."""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax
from common import LEAGUES,COMPONENTS,at

CHOICES=['market']+[f'{kind}:{lam}' for kind in ('global','contextual') for lam in (.1,.01,.001)]
JUNE=at('2026-06-01T00:00:00Z')
JULY=at('2026-07-01T00:00:00Z')


def covariates(row):
    p=np.array(row['components']['market']);v=np.array(row['raw_vote_score']);n=len(p)
    if row['league'] not in LEAGUES:raise ValueError('unsupported league')
    for value in (p,v):
        if value.shape!=(n,) or not np.isfinite(value).all() or (value<0).any() or abs(value.sum()-1)>1e-9:
            raise ValueError('invalid source score vector')
    tops=np.flatnonzero(np.isclose(v,v.max(),rtol=0,atol=1e-12))
    if len(tops)!=1:raise ValueError('ambiguous tipster direction')
    m=(p+v)/2
    def kl(a,b):
        positive=a>0
        return float(np.sum(a[positive]*np.log(a[positive]/b[positive])))
    entropy=-float(np.sum(p[p>0]*np.log(p[p>0])))/np.log(n)
    js=(kl(p,m)+kl(v,m))/(2*np.log(2))
    top=int(tops[0]);gap=float(p.max()-p[top])
    return [1.]+[float(row['league']==league) for league in LEAGUES]+[entropy-.5,js-.5,gap]


def matrices(rows,kind):
    x=np.array([covariates(r) for r in rows],dtype=float)
    if kind=='global':x=x[:,:1]
    if kind not in ('global','contextual'):raise ValueError('invalid family')
    p=np.zeros((len(rows),len(COMPONENTS),3))
    for i,row in enumerate(rows):
        n=len(row['sides'])
        for k,name in enumerate(COMPONENTS):
            v=np.asarray(row['components'][name],dtype=float)
            if v.shape!=(n,) or (v<0).any() or not np.isfinite(v).all() or abs(v.sum()-1)>1e-9:raise ValueError('bad component')
            p[i,k,:n]=v
    return x,p


def weights(theta,x):
    logits=np.column_stack((np.zeros(len(x)),x@theta))
    return softmax(logits,axis=1)


def objective(flat,x,true_probability,lam,penalty):
    theta=flat.reshape(x.shape[1],3)
    w=weights(theta,x);mixed=np.sum(w*true_probability,axis=1)
    if (mixed<=0).any():raise ValueError('zero mixture probability')
    loss=-np.log(mixed).mean()+.5*lam*np.sum(penalty[:,None]*theta**2)
    d=w[:,1:]*(1-true_probability[:,1:]/mixed[:,None])/len(x)
    gradient=x.T@d+lam*penalty[:,None]*theta
    return float(loss),gradient.ravel()


@dataclass
class Stack:
    name:str
    theta:object=None
    gradient_max:float=0.
    fit_n:int=0

    def predict(self,rows):
        if self.name=='market':
            return [r['components']['market'][:] for r in rows],[[1.,0.,0.,0.] for _ in rows]
        kind,_=self.name.split(':');x,p=matrices(rows,kind)
        w=weights(np.asarray(self.theta),x);pred=np.einsum('nk,nkc->nc',w,p)
        return [pred[i,:len(r['sides'])].tolist() for i,r in enumerate(rows)],w.tolist()

    def record(self):
        return {'name':self.name,'theta':self.theta,'gradient_max':self.gradient_max,'fit_n':self.fit_n}


def fit(name,rows,labels,cutoff):
    if not rows:raise ValueError('empty fitting set')
    if any(at(r['decision_at'])>=cutoff or at(labels[r['id']]['available_at_proxy'])>cutoff for r in rows):
        raise ValueError('label or forecast unavailable at fitting cutoff')
    if name=='market':return Stack(name,fit_n=len(rows))
    kind,lam=name.split(':');lam=float(lam)
    x,p=matrices(rows,kind);y=np.array([labels[r['id']]['outcome'] for r in rows],dtype=int)
    if any(not 0<=y[i]<len(r['sides']) for i,r in enumerate(rows)):raise ValueError('invalid outcome')
    true_probability=np.array([p[i,:,y[i]] for i in range(len(rows))])
    penalty=np.ones(x.shape[1])
    if kind=='contextual':penalty[1:1+len(LEAGUES)]=2.
    result=minimize(objective,np.zeros(x.shape[1]*3),args=(x,true_probability,lam,penalty),jac=True,
                    method='L-BFGS-B',options={'maxiter':1000,'ftol':1e-12,'gtol':1e-8})
    value,gradient=objective(result.x,x,true_probability,lam,penalty)
    if not np.isfinite(value) or not np.isfinite(result.x).all() or np.max(np.abs(gradient))>1e-6:
        raise RuntimeError('unconverged fit: '+str(result.message))
    return Stack(name,result.x.reshape(x.shape[1],3).tolist(),float(np.max(np.abs(gradient))),len(rows))


def loss(model,rows,labels):
    pred,_=model.predict(rows)
    return float(np.mean([-np.log(max(p[labels[r['id']]['outcome']],1e-15)) for r,p in zip(rows,pred)]))


def study(rows,labels):
    train=[r for r in rows if at(r['decision_at'])<JUNE and at(labels[r['id']]['available_at_proxy'])<=JUNE]
    validation=[r for r in rows if JUNE<=at(r['decision_at'])<JULY and at(labels[r['id']]['available_at_proxy'])<=JULY]
    refit=[r for r in rows if at(r['decision_at'])<JULY and at(labels[r['id']]['available_at_proxy'])<=JULY]
    test=[r for r in rows if at(r['decision_at'])>=JULY]
    scores={};models={}
    for name in CHOICES:
        model=fit(name,train,labels,JUNE)
        scores[name]=loss(model,validation,labels)
    def winner(names):
        best=names[0]
        for name in names[1:]:
            if scores[name]<scores[best]-1e-10:best=name
        return best
    selected=winner(CHOICES)
    names={'selected':selected,'global':winner([n for n in CHOICES if n.startswith('global:')]),
           'contextual':winner([n for n in CHOICES if n.startswith('contextual:')])}
    cache={}
    for label,name in names.items():
        if name not in cache:cache[name]=fit(name,refit,labels,JULY)
        models[label]=cache[name]
    return {'selected':selected,'validation_logloss':scores,'train_n':len(train),'validation_n':len(validation),
            'refit_n':len(refit),'test_n':len(test),'fits':{k:v.record() for k,v in models.items()}},models,test
