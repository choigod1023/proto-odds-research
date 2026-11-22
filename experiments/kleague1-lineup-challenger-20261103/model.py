"""K1 independent-Poisson ablation; generic numerical/history engine adapted
from K2 preregistration commit 7ba2ce8cfccd3e097014f95ba3b03560870d2f9a.
No K2 fitted coefficient, alpha selection, predictions or results are imported.
"""
import collections, datetime, math
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import skellam

def history_features(games):
 """Only completed XI from strictly earlier calendar dates; never reads any score."""
 hist=collections.defaultdict(list); output={}
 for day, rows_iter in __import__('itertools').groupby(sorted(games,key=lambda x:(x['date'],x['id'])),key=lambda x:x['date']):
  rows=list(rows_iter)
  for g in rows:
   features=[]
   for team,xi in zip(g['teams'],g['xi']):
    prior=hist[team]; current=set(xi);recent=prior[-3:]
    continuity=len(current.intersection(prior[-1]))/11 if prior else 0.0
    recent_starts=sum(len(current.intersection(earlier)) for earlier in recent)/11
    features.append([continuity,recent_starts,float(not prior),float(len(prior)<3)])
   output[g['id']]=features
  same_day=collections.Counter(team for g in rows for team in g['teams'])
  for g in rows:
   for team,xi in zip(g['teams'],g['xi']):
    if same_day[team]==1:hist[team].append(set(xi))
 return output

def extra(own,opp):return np.array([own[0],opp[0],own[1],opp[1],own[2],opp[2],own[3],opp[3]],dtype=float)
def design(games, feats, teams, candidate):
 n=len(games);size=2+2*len(teams)+(8 if candidate else 0);X=np.zeros((2*n,size));unknown=[]
 for i,g in enumerate(games):
  h,a=g['teams'];unknown.append([h not in teams,a not in teams])
  for j,(atk,opp) in enumerate(((h,a),(a,h))):
   row=X[2*i+j];row[0]=1;row[1]=1 if j==0 else 0
   if atk in teams:row[2+teams[atk]]=1
   if opp in teams:row[2+len(teams)+teams[opp]]=1
   if candidate:row[-8:]=extra(feats[g['id']][j],feats[g['id']][1-j])
 return X,unknown

def fit(games,feats,alpha,candidate,cutoff):
 if not games or any(g['date']>cutoff for g in games):raise ValueError('empty or embargoed training fixture')
 teams={s:i for i,s in enumerate(sorted({t for g in games for t in g['teams']}))}
 X,_=design(games,feats,teams,candidate);y=np.array([v for g in games for v in g['score']],dtype=float)
 weights=np.repeat(np.array([math.exp(-math.log(2)*(cutoff-g['date']).days/180) for g in games]),2)
 penalty=np.ones(X.shape[1]);penalty[0]=0
 def obj(beta):
  z=X@beta
  if np.max(np.abs(z))>30:return 1e50,np.ones_like(beta)*1e20
  rates=np.exp(z);loss=np.dot(weights,rates-y*z+gammaln(y+1))+.5*alpha*np.dot(penalty*beta,beta)
  grad=X.T@(weights*(rates-y))+alpha*penalty*beta
  return float(loss),grad
 result=minimize(obj,np.zeros(X.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-12,'gtol':1e-7})
 if not result.success:raise RuntimeError('optimizer failure: '+str(result.message))
 return {'coeff':result.x,'teams':teams,'alpha':alpha,'candidate':candidate,'optimizer_message':str(result.message)}

def predict(model,games,feats):
 X,unknown=design(games,feats,model['teams'],model['candidate']);rates=np.exp(X@model['coeff']).reshape((-1,2))
 p=np.stack([skellam.sf(0,rates[:,0],rates[:,1]),skellam.pmf(0,rates[:,0],rates[:,1]),skellam.cdf(-1,rates[:,0],rates[:,1])],axis=1)
 if not np.all(np.isfinite(p)) or np.max(np.abs(p.sum(axis=1)-1))>1e-10 or np.any(p<0):raise ValueError('invalid probabilities')
 return rates,p,unknown

def score_loss(games,rates):
 y=np.array([g['score'] for g in games]);return float(np.mean(np.sum(rates-y*np.log(rates)+gammaln(y+1),axis=1)))
