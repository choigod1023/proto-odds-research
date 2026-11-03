import collections,math
from datetime import date
import numpy as np
import core,metrics
def outcome_pair(left,right,labels):
 a,b=core.unique(left),core.unique(right)
 if set(a)!=set(b):raise ValueError("outcome pairing mismatch")
 groups=collections.defaultdict(lambda:np.zeros(4));changes=0
 for fid in sorted(a,key=lambda f:(a[f]["event_date"],f)):
  truth=metrics.winner(labels[fid]);x,y=a[fid]["probabilities_3way"],b[fid]["probabilities_3way"];values=[]
  for q in (x,y):values.append(np.array([int(max(("home","draw","away"),key=q.get)==truth),-math.log(max(q[truth],1e-15)),sum((q[k]-(k==truth))**2 for k in ("home","draw","away"))]))
  groups[date.fromisoformat(a[fid]["event_date"]).isocalendar()[:2]]+=np.r_[1,values[0]-values[1]];changes+=max(("home","draw","away"),key=x.get)!=max(("home","draw","away"),key=y.get)
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),(5000,len(arr)))].sum(1)
 return {"weeks":len(arr),"changed3way_picks":changes,**{key:np.quantile(boot[:,j]/boot[:,0],[.025,.975]).tolist() for j,key in enumerate(("accuracy_delta_ci95","logloss_delta_ci95","brier_delta_ci95"),1)}}
