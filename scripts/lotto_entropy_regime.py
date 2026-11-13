"""Exploratory entropy-regime test with past-only inputs and bounded resources."""
import os
for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'):os.environ[key]='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'entropy_support'))
import lotto_nonlinear as resource
import json,math,time,hashlib
import numpy as np

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/lotto/entropy-regime'
COUNTS=np.zeros(64)
for a in range(7):
 for b in range(7-a):
  for c in range(7-a-b):
   d=6-a-b-c
   COUNTS[8*(a+b)+a+c]+=math.comb(11,a)*math.comb(11,b)*math.comb(12,c)*math.comb(11,d)
BASE=COUNTS/COUNTS.sum();VALID=BASE>0;MAXH=math.log2(math.comb(45,6))
NAMES=['entropy_ratio','entropy_change_20','surprise_residual_20','surprise_residual_50','surprise_residual_100','residual_change_20']

def labels(draws):return 8*(draws<=22).sum(1)+(draws%2).sum(1)

def states(y):
 # row t contains pre-draw information, including row len(y) for next draw.
 counts=np.zeros(64);mass=0.;hs=[];res=[];features=[]
 for t in range(len(y)+1):
  p=(counts+100*BASE)/(mass+100)
  h=MAXH-float(np.sum(p[VALID]*np.log2(p[VALID]/BASE[VALID])))
  hs.append(h/MAXH)
  mean=lambda w:float(np.mean(res[-w:])) if res else 0.
  features.append([hs[-1],hs[-1]-hs[max(0,t-20)],mean(20),mean(50),mean(100),mean(20)-(float(np.mean(res[-40:-20])) if len(res)>=40 else 0.)])
  if t<len(y):
   res.append(-math.log2(p[y[t]]/COUNTS[y[t]])-h)
   counts*=.98;counts[y[t]]+=1;mass=.98*mass+1
 return np.array(features)

def evaluate(y):
 f=states(y);train=slice(100,740);test=slice(740,len(y))
 base=(np.bincount(y[train],minlength=64)+100*BASE)/(640+100)
 values=[];predictions=[];thresholds=[]
 for j in range(6):
  threshold=float(np.median(f[train,j]));state=(f[:,j]>threshold).astype(int)
  count=np.bincount(state[train]*64+y[train],minlength=128).reshape(2,64)
  p=(count+100*base)/(count.sum(1)[:,None]+100)
  gain=np.log(p[state[test],y[test]]/base[y[test]])
  values.append(float(gain.sum()));predictions.append(p[state[-1]].tolist());thresholds.append(threshold)
 return np.array(values),{'next_probabilities':predictions,'thresholds':thresholds,'next_features':f[-1].tolist(),'baseline':base.tolist()}

def tail(ref,x):return float((1+np.count_nonzero(np.asarray(ref)>=x))/(len(ref)+1))
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
 resource.constrain();start=time.monotonic();cpu=time.process_time()
 protocol={'seed':2026101706,'train':[101,740],'evaluation':[741,1245],'rules':NAMES,
 'decay':.98,'feature_prior':100,'conditional_prior':100,'threshold':'training median',
 'permutations':199,'iid_histories':199,'family':'max held-out total log gain across six rules',
 'resource':'one CPU BelowNormal; 25 percent rest; 600 second budget',
 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 save('protocol.json',protocol)
 rows=[json.loads(l) for name in ('base.jsonl','extra.jsonl') for l in (OUT/name).read_text().splitlines()]
 assert [r['draw_no'] for r in rows]==list(range(1,1246))
 d=np.array([r['numbers_sorted'] for r in rows]);y=labels(d)
 actual,params=evaluate(y);rng=np.random.default_rng(protocol['seed']);perm=[];iid=[]
 for i in range(199):
  t=time.monotonic();perm.append(evaluate(y[rng.permutation(len(y))])[0])
  iid.append(evaluate(rng.choice(64,len(y),p=BASE))[0])
  assert time.monotonic()-start<600
  time.sleep(.25*(time.monotonic()-t))
 perm=np.array(perm);iid=np.array(iid)
 result={'scores':dict(zip(NAMES,actual.tolist())),'family_permutation_p':tail(perm.max(1),actual.max()),
 'family_iid_p':tail(iid.max(1),actual.max()),'parameters':params,'wall_seconds':time.monotonic()-start,
 'cpu_seconds':time.process_time()-cpu,'peak_memory_mib':resource.peak_mib()}
 save('results.json',result);np.savez_compressed(OUT/'scores.npz',actual=actual,permutations=perm,iid=iid)
 print(json.dumps({k:v for k,v in result.items() if k!='parameters'}),flush=True)

if __name__=='__main__':main()
