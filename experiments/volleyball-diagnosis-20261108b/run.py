"""Offline retrospective price-only control; no operational inputs or fitting on 2026."""
import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[k]='1'
import gzip, json, hashlib
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
P=Path(__file__).resolve().parent
ROOT=P.parents[1]
SOURCE=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'

def fit(rows):
 p=np.array([market(r) for r in rows]); y=np.array([r['y']==0 for r in rows],dtype=float); x=logit(p)
 def loss(b):
  z=b[0]+b[1]*x
  return np.mean(np.logaddexp(0,z)-y*z)+.01*np.sum((b-np.array([0.,1.]))**2)
 r=minimize(loss,[0.,1.],method='BFGS')
 if not r.success and np.linalg.norm(r.jac)>1e-4: raise RuntimeError(r.message)
 return r.x

def market(r):
 a,b=r['odds']; return (1/a)/(1/a+1/b)

def evaluate(rows, probs):
 y=np.array([r['y']==0 for r in rows]); p=np.array(probs); c=p>=.5
 odds=np.array([r['odds'][0 if choice else 1] for r,choice in zip(rows,c)])
 hit=c==y; net=odds*hit-1; wealth=np.r_[0,np.cumsum(net)]; dd=(np.maximum.accumulate(wealth)-wealth).max()
 return {'n':len(rows),'coverage':1.,'accuracy':float(hit.mean()),'mean_odds':float(odds.mean()),'roi':float(net.mean()),'log_loss':float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))),'brier':float(np.mean((p-y)**2)),'max_drawdown_units':float(dd)}

def run():
 rows=[json.loads(x) for x in gzip.open(SOURCE,'rt',encoding='utf8')]; rows=[r for r in rows if r['sport']=='vl']; out=[]; result={'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'model':'2025 league-specific ridge logistic: intercept and slope, penalty 0.01 toward (0,1)','candidate_status':'blocked: no set scores, match format, pregame price receipt or independently verified official result','groups':{}}
 for league in ('KOVO남','KOVO여','남배네이','여배네이'):
  train=[r for r in rows if r['league']==league and r['day'][:4]=='2025']; test=[r for r in rows if r['league']==league and r['day'][:4]=='2026'];
  if not train or not test: continue
  b=fit(train); m=np.array([market(r) for r in test]); q=expit(b[0]+b[1]*logit(m)); groups={'all':list(range(len(test)))}
  for month in sorted({r['day'][:7] for r in test}): groups[month]=[i for i,r in enumerate(test) if r['day'].startswith(month)]
  stats={}
  for name,idx in groups.items():
   rr=[test[i] for i in idx]; mm=m[idx]; qq=q[idx]; a=evaluate(rr,mm); z=evaluate(rr,qq)
   # Paired, day-block bootstrap for differences, preserving within-day events.
   days=sorted({r['day'] for r in rr}); rng=np.random.default_rng(20261108); diffs={k:[] for k in ('log_loss','brier','accuracy','roi')}
   for _ in range(2000):
    sampled=rng.choice(days,len(days),replace=True); ii=[i for day in sampled for i,r in enumerate(rr) if r['day']==day]; y=np.array([rr[i]['y']==0 for i in ii]);
    ma=evaluate([rr[i] for i in ii],mm[ii]); ca=evaluate([rr[i] for i in ii],qq[ii]);
    for key in diffs: diffs[key].append(ca[key]-ma[key])
   stats[name]={'market':a,'logit_control':z,'paired_delta_control_minus_market_day_bootstrap_95':{k:list(map(float,np.quantile(v,[.025,.975]))) for k,v in diffs.items()}}
  result['groups'][league]={'train_n':len(train),'test_n':len(test),'coefficients':list(map(float,b)),'periods':stats}
  for r,pm,pq in zip(test,m,q): out.append({'id':r['id'],'day':r['day'],'league':league,'odds':r['odds'],'home_won':r['y']==0,'market_home_p':float(pm),'logit_home_p':float(pq)})
 with (P/'predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as g:
   for row in out: g.write((json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode())
 (P/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
if __name__=='__main__': run()
