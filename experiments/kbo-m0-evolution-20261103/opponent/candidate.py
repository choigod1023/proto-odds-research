"""Preregistered M0 interaction/recency candidates; no outcomes after 2025."""
import math,sys
from datetime import date
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix,hstack
from scipy.stats import skellam
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'common'))
import core

CONFIGS={
 'interaction10':{'interaction_penalty':10.,'decay_days':None},
 'interaction100':{'interaction_penalty':100.,'decay_days':None},
 'training_decay365':{'interaction_penalty':None,'decay_days':365},
 'training_decay365_interaction100':{'interaction_penalty':100.,'decay_days':365},
}

def training_weights(rows,half_life):
 if not rows:raise ValueError('empty training')
 days=[date.fromisoformat(r['event_date']) for r in rows];last=max(days)
 if half_life is None:w=np.ones(len(rows),dtype=float)
 else:
  if half_life!=365:raise ValueError('unregistered half life')
  w=np.array([2.**(-((last-d).days)/half_life) for d in days],dtype=float);w/=w.mean()
 if not np.isfinite(w).all() or (w<=0).any() or not np.isclose(w.sum(),len(rows)):raise ValueError('invalid normalized training weights')
 return w,last.isoformat()

def features(rows,spec):
 if not rows:return np.empty((0,3))
 nums=np.array([core.legacy.scalar_features(r) for r in rows],dtype=float)
 missing=~np.isfinite(nums);mean=np.asarray(spec['mean'],float);scale=np.asarray(spec['scale'],float)
 z=(np.where(missing,mean,nums)-mean)/scale
 out=np.stack((z[:,:,5]*z[:,:,6],z[:,:,6]*z[:,:,7],z[:,:,2]*z[:,:,7]),axis=2)
 return out.reshape(-1,3)

def design(rows,spec,config):
 normalized=[core.normalize(r) for r in rows];base,nbase=core.legacy.design(normalized,spec,'M0')
 if config['interaction_penalty'] is None:return base,nbase
 return hstack((base,csr_matrix(features(normalized,spec))),format='csr'),nbase

def objective(beta,X,y,weights,pen):
 z=np.asarray(X@beta).reshape(-1)
 if np.max(np.abs(z))>30:return 1e100,np.zeros_like(beta)
 mu=np.exp(z);error=weights*(mu-y)
 loss=float(np.sum(weights*(mu-y*z))+.5*np.dot(pen*beta,beta))
 gradient=np.asarray(X.T@error).reshape(-1)+pen*beta
 return loss,gradient

def fit(raw_rows,labels,config_id):
 if config_id not in CONFIGS:raise ValueError('unregistered config')
 if not raw_rows:raise ValueError('empty training')
 rows=[core.normalize(r) for r in raw_rows]
 if len({r['fixture_id'] for r in rows})!=len(rows):raise ValueError('duplicate fixtures')
 cfg=CONFIGS[config_id];spec=core.legacy.make_spec(rows)
 # M0 has no player identity coefficients or vocabulary.
 spec['hitters']=[];spec['starters']=[]
 X,nbase=design(rows,spec,cfg);y=core.legacy.target(rows,labels,'M0');weights,last=training_weights(rows,cfg['decay_days']);w=np.repeat(weights,2)
 pen=np.array([0.]+[10.]*(nbase-1)+([cfg['interaction_penalty']]*3 if cfg['interaction_penalty'] is not None else []),dtype=float)
 if X.shape!=(len(y),len(pen)):raise ValueError('design shape mismatch')
 init=np.zeros(len(pen));init[0]=math.log(max(float(np.average(y,weights=w)),.1))
 sol=minimize(objective,init,args=(X,y,w,pen),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
 if not sol.success or not np.isfinite(sol.fun) or not np.isfinite(sol.x).all():raise ValueError('optimizer failure: '+str(sol.message))
 return {'family':'opponent','config_id':config_id,'config':dict(cfg),'spec':spec,'beta':sol.x.tolist(),'nbase':nbase,'penalties':pen.tolist(),'objective':float(sol.fun),'iterations':int(sol.nit),'gradient_max':float(np.max(np.abs(sol.jac))),'convergence':str(sol.message),'n_train':len(rows),'weight_anchor_training_date':last,'weight_sum':float(weights.sum()),'weight_min':float(weights.min()),'weight_max':float(weights.max())}

def predict(model,raw_rows):
 cfg=CONFIGS[model['config_id']]
 if model['config']!=cfg:raise ValueError('configuration mismatch')
 rows=[core.normalize(r) for r in raw_rows];X,nbase=design(rows,model['spec'],cfg);beta=np.asarray(model['beta'],float)
 if nbase!=model['nbase'] or X.shape[1]!=len(beta):raise ValueError('model design mismatch')
 z=np.asarray(X@beta).reshape(-1)
 if np.any(np.abs(z)>20):raise ValueError('unstable rate')
 rates=np.exp(z).reshape(-1,2);h=rates[:,0];aw=rates[:,1];p=np.column_stack((skellam.sf(0,h,aw),skellam.pmf(0,h,aw),skellam.cdf(-1,h,aw)));p=p/p.sum(axis=1)[:,None]
 if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1,atol=1e-12):raise ValueError('invalid probabilities')
 result=[]
 for i,r in enumerate(rows):
  denominator=p[i,0]+p[i,2]
  if denominator<=0:raise ValueError('no decisive probability')
  result.append({'fixture_id':r['fixture_id'],'event_date':r['event_date'],'probabilities_3way':{'home':float(p[i,0]),'draw':float(p[i,1]),'away':float(p[i,2])},'probabilities_2way':{'home':float(p[i,0]/denominator),'away':float(p[i,2]/denominator)},'home_rate':float(rates[i,0]),'away_rate':float(rates[i,1])})
 return result
