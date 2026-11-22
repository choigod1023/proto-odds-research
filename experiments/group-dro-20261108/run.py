import gzip,json,hashlib,pathlib,collections
import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax,logsumexp
P=pathlib.Path(__file__).resolve().parent
ROOT=P.parents[1]
BASE=ROOT/'experiments/all-sports-underdog-20261031/results'
GROUPS={'bs':['MLB','NPB','KBO'],'bk':['NBA','KBL','WKBL'],'sc':['EPL','MLS']}
RIDGE=.01

def load(s):
 p=BASE/s/'domestic_archive/ledger.jsonl.gz'
 rows=[json.loads(x) for x in gzip.open(p,'rt',encoding='utf-8')]
 rows=[r for r in rows if r['league'] in GROUPS[s] and '2025-01-01'<=r['day']<'2027-01-01']
 rows.sort(key=lambda r:(r['day'],r['id']))
 assert len({r['id'] for r in rows})==len(rows)
 assert all(len(r['market_p'])==len(r['odds'])==(3 if s=='sc' else 2) and 0<=r['y']<len(r['odds']) and min(r['odds'])>1 for r in rows)
 return rows,hashlib.sha256(p.read_bytes()).hexdigest()

def arrays(rows):
 m=np.array([r['market_p'] for r in rows]); y=np.array([r['y'] for r in rows]);
 # intercept and log market odds relative to geometric mean, a shared calibration basis
 x=np.column_stack([np.ones(len(rows)),np.log(m[:,0]/m[:,-1])])
 return x,np.log(m),y

def fit(rows,mode):
 x,z,y=arrays(rows); n,k=z.shape; groups=np.array([r['league'] for r in rows]); names=sorted(set(groups)); oh=np.eye(k)[y]
 def calc(b):
  b=b.reshape(2,k); logits=z+x@b; losses=logsumexp(logits,axis=1)-logits[np.arange(n),y]; err=softmax(logits,axis=1)-oh
  lg=np.array([losses[groups==g].mean() for g in names]); gg=np.array([x[groups==g].T@err[groups==g]/sum(groups==g) for g in names]);
  mean=losses.mean(); grad=x.T@err/n
  penalty=RIDGE*(b*b).sum(); pg=2*RIDGE*b
  return mean,grad,lg,gg,penalty,pg
 if mode=='erm':
  def obj(b):
   a,g,_,_,p,pg=calc(b); return a+p,(g+pg).ravel()
  res=minimize(obj,np.zeros(2*k),jac=True,method='BFGS',options={'gtol':1e-8,'maxiter':2000})
  if np.linalg.norm(res.jac)>1e-5: raise RuntimeError(res.message)
  return res.x.reshape(2,k)
 # 50% empirical loss + 50% maximum pre-defined league loss, epigraph formulation
 def obj(v):
  a,g,_,_,p,pg=calc(v[:-1]); return .5*a+.5*v[-1]+p,np.r_[ (.5*g+pg).ravel(),.5]
 def con(v): return v[-1]-calc(v[:-1])[2]
 def jac(v):
  gg=calc(v[:-1])[3]; return np.column_stack([-gg.reshape(len(names),-1),np.ones(len(names))])
 res=minimize(obj,np.r_[np.zeros(2*k),2.],jac=True,constraints=[{'type':'ineq','fun':con,'jac':jac}],method='SLSQP',options={'ftol':1e-11,'maxiter':2000})
 if not res.success or min(con(res.x)) < -1e-7: raise RuntimeError(res.message)
 return res.x[:-1].reshape(2,k)

def predict(rows,b):
 x,z,y=arrays(rows); return softmax(z+x@b,axis=1)

def metrics(rows,probs):
 y=np.array([r['y'] for r in rows]); pick=probs.argmax(axis=1); price=np.array([r['odds'] for r in rows])[np.arange(len(y)),pick]; hit=pick==y; net=price*hit-1
 cum=np.r_[0,np.cumsum(net)]; dd=float(np.max(np.maximum.accumulate(cum)-cum));
 return {'n':len(y),'accuracy':float(hit.mean()),'log_loss':float(-np.log(probs[np.arange(len(y)),y]).mean()),'brier':float(((probs-np.eye(probs.shape[1])[y])**2).sum(axis=1).mean()),'roi':float(net.mean()),'max_drawdown_units':dd}

def run():
 out={}; forecasts=[]; hashes={}
 for s in GROUPS:
  rows,h=load(s); hashes[s]=h; train=[r for r in rows if r['day']<'2026-01-01']; test=[r for r in rows if r['day']>='2026-01-01']; assert set(r['league'] for r in train)==set(GROUPS[s])==set(r['league'] for r in test)
  ps={a:predict(test,fit(train,a)) for a in ('erm','dro')};
  def subset(ids): return {a:metrics([test[i] for i in ids],p[ids]) for a,p in ps.items()}
  groups={g:subset([i for i,r in enumerate(test) if r['league']==g]) for g in GROUPS[s]}; months={m:subset([i for i,r in enumerate(test) if r['day'][:7]==m]) for m in sorted({r['day'][:7] for r in test})}
  # Paired day-block bootstrap for overall difference; descriptive, not simultaneous league inference.
  days=sorted({r['day'] for r in test}); idx={d:np.array([i for i,r in enumerate(test) if r['day']==d]) for d in days}; rng=np.random.default_rng(20261108); diffs=[]
  for _ in range(1000):
   ii=np.concatenate([idx[d] for d in rng.choice(days,len(days),replace=True)]); sub=[test[i] for i in ii]; mm={a:metrics(sub,p[ii]) for a,p in ps.items()}; diffs.append([mm['dro'][v]-mm['erm'][v] for v in ('accuracy','log_loss','brier','roi')])
  out[s]={'train_n':len(train),'test_n':len(test),'train_groups':dict(collections.Counter(r['league'] for r in train)),'overall':subset(list(range(len(test)))),'leagues':groups,'months':months,'paired_day_bootstrap_95':dict(zip(('accuracy','log_loss','brier','roi'),np.quantile(diffs,[.025,.975],axis=0).T.tolist()))}
  for i,r in enumerate(test):
   f={k:r[k] for k in ('id','day','league','sport','home','away','odds','market_p','y','sources','kickoff_precision')}; f['models']={}
   for a,p in ps.items():
    pick=int(np.argmax(p[i])); f['models'][a]={'probabilities':p[i].tolist(),'pick':pick,'hit':pick==r['y'],'net':r['odds'][pick]*(pick==r['y'])-1}
   forecasts.append(f)
 (P/'predictions.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in forecasts),encoding='utf8')
 (P/'summary.json').write_text(json.dumps({'input_sha256':hashes,'results':out},indent=2,ensure_ascii=False),encoding='utf8')
 print(json.dumps({s:v['overall'] for s,v in out.items()},indent=2))
if __name__=='__main__': run()
