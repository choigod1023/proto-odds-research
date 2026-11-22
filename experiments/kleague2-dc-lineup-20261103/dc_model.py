"""Dixon-Coles correction for the frozen K2 Poisson design; diagnostic only."""
import datetime,hashlib,importlib.util,math,sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson,skellam
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1];PRIOR=REPO/'experiments/kleague2-lineup-challenger-20261103'
spec=importlib.util.spec_from_file_location('k2_base',PRIOR/'model.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
sys.path.insert(0,str(REPO))
from src.score_dist import dixon_coles_tau
EPS=1e-6
UPPER=np.array([1.,1.,3.,3.,1.,1.,1.,1.])

def validate_features(games,feats):
 for g in games:
  sides=feats.get(g['id'])
  if sides is None or len(sides)!=2:raise ValueError('missing XI feature')
  for x in sides:
   if len(x)!=4 or any(not math.isfinite(v) for v in x):raise ValueError('invalid feature')
   if not (0<=x[0]<=1 and 0<=x[1]<=3 and x[2] in (0,1) and x[3] in (0,1)):raise ValueError('outside XI feature domain')

def require_cohort(games,original_rows,features):
 if [g['id'] for g in games]!=[r['fixture_id'] for r in original_rows]:raise ValueError('external or reordered test cohort')
 for g,r in zip(games,original_rows):
  actual=hashlib.sha256(__import__('json').dumps(features[g['id']],separators=(',',':')).encode('utf8')).hexdigest()
  if actual!=r['features_sha256'] or g['kickoff']!=r['kickoff_kst'] or g['teams']!=r['team_ids']:raise ValueError('original feature or identity mismatch')

def rho_transform(theta,n,candidate):
 """Global safe envelope over all teams, unseen team and valid XI features."""
 grad=np.zeros_like(theta);grad[0]=1.;mx=float(theta[0])
 for start in (2,2+n):
  values=theta[start:start+n]
  if n and values.max()>0:
   index=int(np.argmax(values));mx+=float(values[index]);grad[start+index]=1.
 if candidate:
  for i,upper in enumerate(UPPER):
   idx=2+2*n+i
   if theta[idx]>0:mx+=float(theta[idx])*upper;grad[idx]=upper
 home_log=mx+float(theta[1]);away_log=mx
 if max(home_log,away_log)>30:raise ValueError('rate envelope too large')
 gh=grad.copy();gh[1]+=1;ga=grad.copy()
 mh=math.exp(home_log);ma=math.exp(away_log)
 pos=min(.3,(1-EPS)/(mh*ma));neg=min(.3,(1-EPS)/max(mh,ma));t=math.tanh(theta[-1])
 if t>=0:
  magnitude=pos;dm=-magnitude*(gh+ga) if pos<.3 else np.zeros_like(theta)
 else:
  magnitude=neg;dm=-magnitude*(gh if mh>=ma else ga) if neg<.3 else np.zeros_like(theta)
 derivative=t*dm;derivative[-1]+=magnitude*(1-t*t)
 return float(t*magnitude),derivative,{'max_home_rate':mh,'max_away_rate':ma,'rho_low':-neg,'rho_high':pos}

def all_tau(rates,rho):
 h=rates[:,0];a=rates[:,1]
 t=np.column_stack([dixon_coles_tau(0,0,h,a,rho),dixon_coles_tau(0,1,h,a,rho),dixon_coles_tau(1,0,h,a,rho),np.broadcast_to(dixon_coles_tau(1,1,h,a,rho),h.shape)])
 if not np.all(np.isfinite(t)) or np.min(t)<=0:raise ValueError('nonpositive tau')
 return t

def objective(games,feats,alpha,candidate,cutoff):
 validate_features(games,feats)
 teams={s:i for i,s in enumerate(sorted({t for g in games for t in g['teams']}))};X,_=base.design(games,feats,teams,candidate);n=len(teams)
 y=np.array([g['score'] for g in games],dtype=float);weights=np.array([math.exp(-math.log(2)*(cutoff-g['date']).days/180) for g in games]);penalty=np.ones(X.shape[1]);penalty[0]=0
 def fn(theta):
  z=X@theta[:-1]
  if not np.all(np.isfinite(z)) or np.max(np.abs(z))>30:return 1e50,np.zeros_like(theta)
  try:rho,drho,_=rho_transform(theta,n,candidate)
  except (OverflowError,ValueError):return 1e50,np.zeros_like(theta)
  rates=np.exp(z).reshape(-1,2)
  try:tau=all_tau(rates,rho)
  except ValueError:return 1e50,np.zeros_like(theta)
  h,a=y[:,0],y[:,1];lh,la=rates[:,0],rates[:,1];tf=np.ones(len(games));dh=np.zeros(len(games));da=dh.copy();dr=dh.copy()
  for i,j,col in ((0,0,0),(0,1,1),(1,0,2),(1,1,3)):
   mask=(h==i)&(a==j);tf[mask]=tau[mask,col]
   if (i,j)==(0,0):dh[mask]=da[mask]=-lh[mask]*la[mask]*rho;dr[mask]=-lh[mask]*la[mask]
   elif (i,j)==(0,1):dh[mask]=lh[mask]*rho;dr[mask]=lh[mask]
   elif (i,j)==(1,0):da[mask]=la[mask]*rho;dr[mask]=la[mask]
   else:dr[mask]=-1
  loss=np.sum(weights*(lh-h*np.log(lh)+gammaln(h+1)+la-a*np.log(la)+gammaln(a+1)-np.log(tf)))+.5*alpha*np.dot(penalty*theta[:-1],theta[:-1])
  gh=weights*(lh-h-dh/tf);ga=weights*(la-a-da/tf);inter=np.column_stack((gh,ga)).reshape(-1)
  gradient=np.r_[X.T@inter+alpha*penalty*theta[:-1],0.] - np.sum(weights*dr/tf)*drho
  return float(loss),gradient
 return fn,teams,X.shape[1]

def fit(games,feats,alpha,candidate,cutoff):
 fn,teams,size=objective(games,feats,alpha,candidate,cutoff)
 result=minimize(fn,np.zeros(size+1),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-12,'gtol':1e-7})
 if not result.success or not np.isfinite(result.fun):raise RuntimeError('optimizer failure '+str(result.message))
 rho,_,bounds=rho_transform(result.x,len(teams),candidate)
 return {'teams':teams,'theta':result.x,'candidate':candidate,'alpha':alpha,'rho':rho,'rho_bounds':bounds,'optimizer_message':str(result.message)}

def predict(fit,games,feats):
 validate_features(games,feats);X,unknown=base.design(games,feats,fit['teams'],fit['candidate']);rates=np.exp(X@fit['theta'][:-1]).reshape(-1,2);rho=fit['rho'];tau=all_tau(rates,rho)
 h,a=rates[:,0],rates[:,1];p=np.stack([skellam.sf(0,h,a),skellam.pmf(0,h,a),skellam.cdf(-1,h,a)],axis=1)
 for i,j,col in ((0,0,0),(0,1,1),(1,0,2),(1,1,3)):
  delta=poisson.pmf(i,h)*poisson.pmf(j,a)*(tau[:,col]-1)
  p[:,0 if i>j else 1 if i==j else 2]+=delta
 if not np.all(np.isfinite(p)) or np.any(p<0) or not np.allclose(p.sum(axis=1),1,atol=3e-12,rtol=0):raise ValueError('invalid score probabilities')
 return rates,p,unknown,float(tau.min())

def score_loss(games,rates,rho):
 y=np.array([g['score'] for g in games]);tau=all_tau(rates,rho);tf=np.ones(len(games))
 for i,j,k in ((0,0,0),(0,1,1),(1,0,2),(1,1,3)):mask=(y[:,0]==i)&(y[:,1]==j);tf[mask]=tau[mask,k]
 return float(np.mean(np.sum(rates-y*np.log(rates)+gammaln(y+1),axis=1)-np.log(tf)))
