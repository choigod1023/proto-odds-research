import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[k]='1'
import collections,gzip,hashlib,json,sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from matches import load_matches

def fit(train):
 x=np.array([[1.,r['feature']] for r in train]); m=np.array([r['market'] for r in train]);y=np.array([r['y'] for r in train]);
 def fun(b):
  z=logit(m)+x@b;return np.mean(np.logaddexp(0,z)-y*z)+np.sum(b*b)
 res=minimize(fun,[0.,0.]);
 if not res.success and np.linalg.norm(res.jac)>1e-4:raise ValueError(res.message)
 return res.x

def metrics(rows,probs):
 y=np.array([r['y'] for r in rows]);p=np.clip(np.asarray(probs),1e-9,1-1e-9);pick=p>=.5;odds=np.array([r['odds'][0 if c else 1] for r,c in zip(rows,pick)]);hit=pick==y;net=odds*hit-1;w=np.r_[0,np.cumsum(net)];return {'n':len(y),'accuracy':float(np.mean(hit)),'mean_odds':float(odds.mean()),'roi':float(net.mean()),'log_loss':float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))),'brier':float(np.mean((p-y)**2)),'mdd':float(np.max(np.maximum.accumulate(w)-w))}

def daily_features(rows):
 history=collections.defaultdict(lambda:collections.deque(maxlen=10));out=[]
 for day,group in __import__('itertools').groupby(sorted(rows,key=lambda r:(r['day'],r['id'])),key=lambda r:r['day']):
  group=list(group)
  for r in group:
   h=history[(r['league'],r['home'])];a=history[(r['league'],r['away'])]
   if len(h)>=5 and len(a)>=5:
    z=(sum(h)-sum(a))/60.;r['feature']=z;r['sets_p']=max(.1,min(.9,.5+z));out.append(r.copy())
  for r in group:
   d=r['hs']-r['as'];history[(r['league'],r['home'])].append(d);history[(r['league'],r['away'])].append(-d)
 return out

def main():
 price=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz';raw=ROOT/'data/processed/games.csv.tmp'
 matches=load_matches(path=raw,sports=['vl']);matches=matches[matches.league.isin(['KOVO남','KOVO여'])]; lookup={}
 for r in matches.itertuples(index=False):
  k=(r.league,r.kickoff.isoformat(),r.home_team,r.away_team);lookup[k]=(int(r.home_score),int(r.away_score))
 rows=[];counts=collections.Counter(); price_counts=collections.Counter(); joined_counts=collections.Counter()
 for line in gzip.open(price,'rt',encoding='utf8'):
  r=json.loads(line)
  if r['sport']!='vl' or r['league'] not in ('KOVO남','KOVO여') or r['day'][:4] not in ('2023','2024'):continue
  counts['price_rows']+=1;price_counts[r['league']+'/'+r['day'][:4]]+=1;k=(r['league'],r['kickoff'],r['home'],r['away']);score=lookup.get(k)
  if score is None:counts['score_unmatched']+=1;continue
  h,a=score
  if not (max(h,a)==3 and min(h,a) in (0,1,2) and (h>a)==(r['y']==0)):counts['score_invalid_or_conflict']+=1;continue
  inv=[1/z for z in r['odds']];r.update(hs=h,**{'as':a},market=inv[0]/sum(inv),y=int(r['y']==0));rows.append(r);counts['joined']+=1;joined_counts[r['league']+'/'+r['day'][:4]]+=1
 data=daily_features(rows);out=[];summary={'source_sha256':hashlib.sha256(price.read_bytes()).hexdigest(),'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'counts':dict(counts),'feature_eligible':len(data),'price_counts':dict(price_counts),'joined_counts':dict(joined_counts),'official_event_linked_evaluation':0,'pregame_price_receipt':0,'official_result_availability_time':0,'groups':{},'split':'2023 Jan-Sep train, Oct-Dec validation; 2024 evaluation; retrospective only'}
 for league in ('KOVO남','KOVO여'):
  subset=[r for r in data if r['league']==league];train=[r for r in subset if r['day']<'2023-10-01'];val=[r for r in subset if '2023-10-01'<=r['day']<'2024-01-01'];test=[r for r in subset if r['day'].startswith('2024')];den=sum(r['league']==league and r['day'].startswith('2024') for r in rows)
  if len(train)<30 or not test:summary['groups'][league]={'train':len(train),'validation':len(val),'test':len(test),'reason':'insufficient_training_or_test'};continue
  b=fit(train);m=np.array([r['market'] for r in test]);sp=np.array([r['sets_p'] for r in test]);a=.8*m+.2*(10*sp**3-15*sp**4+6*sp**5);c=expit(logit(m)+b[0]+b[1]*np.array([r['feature'] for r in test]));arr={'market':m,'set_shrink':a,'market_set_residual':c};stats={k:metrics(test,v) for k,v in arr.items()};days=sorted({r['day'] for r in test});rng=np.random.default_rng(20261108);diff={k:{z:[] for z in ('accuracy','roi','log_loss','brier')} for k in ('set_shrink','market_set_residual')}
  for _ in range(2000):
   sampled=rng.choice(days,len(days),replace=True);ii=[i for d in sampled for i,r in enumerate(test) if r['day']==d];rr=[test[i] for i in ii];base=metrics(rr,m[ii]);
   for name in diff:
    v=metrics(rr,arr[name][ii]);
    for metric in diff[name]:diff[name][metric].append(v[metric]-base[metric])
  summary['groups'][league]={'train':len(train),'validation':len(val),'test':len(test),'opportunities_joined':den,'all_price_rows':price_counts[league+'/2024'],'coverage_joined':len(test)/den if den else None,'coverage_all_price_rows':len(test)/price_counts[league+'/2024'],'changed_picks':{name:int(np.sum((v>=.5)!=(m>=.5))) for name,v in arr.items()},'monthly':{month:{name:metrics([r for r in test if r['day'].startswith(month)],v[[i for i,r in enumerate(test) if r['day'].startswith(month)]]) for name,v in arr.items()} for month in sorted({r['day'][:7] for r in test})},'coef':b.tolist(),'metrics':stats,'paired_day_bootstrap_95':{k:{z:list(map(float,np.quantile(v,[.025,.975]))) for z,v in ds.items()} for k,ds in diff.items()}}
  for i,r in enumerate(test):out.append({k:r[k] for k in ('id','day','league','odds','y','feature')}|{k:float(v[i]) for k,v in arr.items()})
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 with (OUT/'predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as g:
   for r in out:g.write((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode())
if __name__=='__main__':main()
