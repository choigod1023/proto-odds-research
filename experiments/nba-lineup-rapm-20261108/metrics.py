import gzip,json,collections,datetime,math
import numpy as np
from pathlib import Path
P=Path(__file__).resolve().parent
with gzip.open(P/'predictions.jsonl.gz','rt',encoding='utf8') as f: rows=[json.loads(x) for x in f]
test=[r for r in rows if r['split']=='test']
def loss(r,k):
 p=r['home_win_probability'][k]; y=int(r['margin']>0)
 return {'correct':int((p>=.5)==bool(y)), 'll':-(y*math.log(p)+(1-y)*math.log(1-p)), 'brier':(p-y)**2,'sqerr':(r['predicted_margin'][k]-r['margin'])**2}
def summary(rs):
 return {k:{'n':len(rs),'accuracy':sum(loss(r,k)['correct'] for r in rs)/len(rs),'ll':sum(loss(r,k)['ll'] for r in rs)/len(rs),'brier':sum(loss(r,k)['brier'] for r in rs)/len(rs),'rmse':math.sqrt(sum(loss(r,k)['sqerr'] for r in rs)/len(rs))} for k in ('team','player','pair')}
weeks=collections.defaultdict(list);months=collections.defaultdict(list)
for r in test:
 dt=datetime.datetime.fromisoformat(r['start'].replace('Z','+00:00')); weeks[f'{dt.isocalendar().year}-W{dt.isocalendar().week:02d}'].append(r);months[r['start'][:7]].append(r)
rng=np.random.default_rng(20261108); wk=list(sorted(weeks)); comparisons={}
for a,b in [('player','team'),('pair','player'),('pair','team')]:
 changed=[r for r in test if (r['home_win_probability'][a]>=.5)!=(r['home_win_probability'][b]>=.5)]
 diffs=[]
 for _ in range(5000):
  sample=[r for j in rng.integers(0,len(wk),size=len(wk)) for r in weeks[wk[j]]]
  diffs.append(sum(loss(r,a)['correct']-loss(r,b)['correct'] for r in sample)/len(sample))
 comparisons[a+'_minus_'+b]={'changed_picks':len(changed),'changed_candidate_correct':sum(loss(r,a)['correct'] for r in changed),'changed_baseline_correct':sum(loss(r,b)['correct'] for r in changed),'accuracy_diff':sum(loss(r,a)['correct']-loss(r,b)['correct'] for r in test)/len(test),'weekly_bootstrap_95_ci':list(np.quantile(diffs,[.025,.975])),'bootstrap_draws':5000}
result={'test_period_utc':[test[0]['start'],test[-1]['start']],'weekly_clusters':len(wk),'monthly':{m:summary(v) for m,v in sorted(months.items())},'paired':comparisons,'test_all':summary(test)}
(P/'metrics-detail.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result,indent=2))
