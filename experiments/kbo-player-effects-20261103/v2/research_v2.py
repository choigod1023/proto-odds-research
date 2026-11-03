"""M2-only correction: preserve v1 contrasts plus symmetric missingness sums."""
import hashlib,json,sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp
from scipy.sparse import csr_matrix,hstack
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent));import research as v1
CONTRACT_SHA='c368b227534310a67a46a7f80b510867ae14e3bb936d3dd8eed03f085d9f06f6'
def verify_contract():
 if hashlib.sha256((P/'correction-contract.json').read_bytes()).hexdigest()!=CONTRACT_SHA:raise ValueError('correction contract SHA mismatch')
 return json.loads((P/'correction-contract.json').read_bytes())
def quality_sums(row,spec):
 nums=np.array(v1.scalar_features(row),dtype=float);missing=(~np.isfinite(nums)).astype(float);quality=np.array([v1.side_features(row,side,spec) for side in ('home','away')]);return np.r_[missing.sum(axis=0),quality.sum(axis=0)]
def design(rows,spec):
 original,nbase=v1.design(rows,spec,'M2');quality=csr_matrix(np.array([quality_sums(r,spec) for r in rows],dtype=float));return hstack((original[:,:nbase],quality,original[:,nbase:]),format='csr'),nbase+12

def fit(rows,labels,player_l2):
 if player_l2 not in v1.PENALTIES:raise ValueError('unregistered player penalty')
 spec=v1.make_spec(rows);X,nbase=design(rows,spec);y=v1.target(rows,labels,'M2');d=X.shape[1];pen=np.r_[np.array([0.]+[10.]*(nbase-1)),np.full(d-nbase,player_l2)]
 def objective(flat):
  beta=flat.reshape(2,d);logits=np.column_stack((X@beta[0],X@beta[1],np.zeros(len(y))));ls=logsumexp(logits,axis=1);prob=np.exp(logits-ls[:,None]);loss=float(np.sum(ls-logits[np.arange(len(y)),y]));grad=np.vstack([X.T@(prob[:,k]-(y==k)) for k in (0,1)]);loss+=.5*float(np.sum(beta*beta*pen));grad+=beta*pen;return loss,grad.ravel()
 sol=minimize(objective,np.zeros(2*d),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
 if not sol.success or not np.isfinite(sol.fun) or not np.isfinite(sol.x).all():raise ValueError('optimizer failure: '+str(sol.message))
 return {'family':'M2_v2','player_l2':player_l2,'spec':spec,'beta':sol.x.tolist(),'nbase':nbase,'objective':float(sol.fun),'iterations':int(sol.nit),'convergence':str(sol.message),'gradient_max':float(np.max(np.abs(sol.jac))),'n_train':len(rows),'quality_sum_columns':12}

def predict(model,rows):
 X,nbase=design(rows,model['spec']);d=X.shape[1];z=X@np.array(model['beta']).reshape(2,d).T;logits=np.column_stack((z,np.zeros(len(rows))));p=np.exp(logits-logsumexp(logits,axis=1)[:,None])
 if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1,atol=1e-12):raise ValueError('invalid probabilities')
 out=[]
 for i,row in enumerate(rows):
  den=p[i,0]+p[i,2]
  if den<=0:raise ValueError('no decisive probability')
  out.append({'fixture_id':row['fixture_id'],'event_date':row['event_date'],'probabilities_3way':{k:float(p[i,j]) for j,k in enumerate(('home','draw','away'))},'probabilities_2way':{'home':float(p[i,0]/den),'away':float(p[i,2]/den)},'home_rate':None,'away_rate':None,'roster_coverage':{side:v1.side_features(row,side,model['spec']) for side in ('home','away')}})
 return out
