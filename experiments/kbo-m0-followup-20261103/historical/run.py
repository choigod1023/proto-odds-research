"""Fixed 2023-fit/2024-predict only; no 2024 outcome or price access."""
import gzip, hashlib, importlib.util, json, math, sys
from datetime import date,timedelta
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;V=ROOT/'vendor/evolution';CONTRACT_SHA='c56b5675def7e54783b6e67c87e09c5d03d405aba90d12fa07b0fb577be8f7ef'

def load_module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
core=load_module('core',V/'common/core.py')
shrink=load_module('followup_historical_shrink',V/'recency/candidate.py')
nb=load_module('followup_historical_nb',V/'distribution/candidate.py')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def verify_files():
 m=json.loads((ROOT/'source-manifest.json').read_bytes())
 if sha(ROOT/'contract.json')!=CONTRACT_SHA or m['contract_sha256']!=CONTRACT_SHA:raise ValueError('followup contract changed')
 if sha(ROOT/'inputs/fit2023-labels.jsonl.gz')!=m['fit2023_labels_sha256']:raise ValueError('fit labels changed')
 # Only verify and read files required here, never the 2024/25 label file.
 for suffix in ('contract.json','common/core.py','common/manifest.json','common/vendor/research.py','common/vendor/original_adapter.py','common/inputs/train-actual.jsonl.gz','common/inputs/source-dates.json','recency/candidate.py','distribution/candidate.py'):
  if sha(V/suffix)!=m['source_files']['experiments/kbo-m0-evolution-20261103/'+suffix]:raise ValueError('source changed: '+suffix)
 if sha(V/'contract.json')!=core.CONTRACT_SHA:raise ValueError('original contract changed')
 return m

def inputs():
 verify_files(); allrows=core.rows('train-actual.jsonl.gz');core.unique(allrows)
 train=sorted((r for r in allrows if r['season']==2023),key=lambda r:(r['event_date'],r['fixture_id']))
 target=sorted((r for r in allrows if r['season']==2024),key=lambda r:(r['event_date'],r['fixture_id']))
 if len(train)!=723 or len(target)!=728:raise ValueError('cohort mismatch')
 boundary=date.fromisoformat(target[0]['event_date'])-timedelta(days=7)
 if any(date.fromisoformat(r['event_date'])>boundary for r in train):raise ValueError('embargo violation')
 known={k:date.fromisoformat(v) for k,v in json.loads((V/'common/inputs/source-dates.json').read_bytes()).items()}
 for r in train+target:
  fid=r['fixture_id'];day=date.fromisoformat(r['event_date'])
  if known[fid]!=day or day.year!=r['season'] or r['historical_receipt_proven'] is not False or r['availability_tier']!='retrospective_unproven':raise ValueError('source identity')
  core.legacy.adapter.check_nested(r['raw_features'],fid,day,known)
  for name,x in r['raw_features'].items():
   if core.legacy.adapter.scalar(name,x['value'])!=r['features'][name]['value']:raise ValueError('scalar mismatch')
  for side in ('home','away'):
   z=r['roster'][side];v=r['raw_features'][side+'_lineup_strength']['value'];s=r['raw_features'][side+'_starter_strength']['value']
   if not z['starter'] or len(z['hitters'])!=9 or len(set(z['hitters']))!=9:raise ValueError('invalid roster')
   if v is not None and [x['player_id'] for x in v]!=z['hitters']:raise ValueError('lineup mismatch')
   if s is not None and s['projected_player_id']!=z['starter']:raise ValueError('starter mismatch')
 with gzip.open(ROOT/'inputs/fit2023-labels.jsonl.gz','rt',encoding='utf8') as f:labels=core.unique([json.loads(x) for x in f])
 if set(labels)!=set(core.unique(train)) or set(labels)&set(core.unique(target)):raise ValueError('labels cohort mismatch')
 for r in train:
  y=labels[r['fixture_id']]
  if y['status']!='final' or not str(y['assumed_completed_date']).startswith('2023') or any(type(y[k]) is not int or y[k]<0 for k in ('home_score','away_score')):raise ValueError('bad fit label')
 return train,target,labels

class PoisonLabels(dict):
 """Reject any request for a non-training label; never provide target labels."""
 def __init__(self,labels):super().__init__(labels);self.seen=set()
 def __getitem__(self,key):
  if key not in self:raise AssertionError('future or unknown label accessed: '+str(key))
  self.seen.add(key);return super().__getitem__(key)

def fit_components(train,labels):
 guard=PoisonLabels(labels)
 models={'baseline':core.fit_baseline(train,guard),'shrink':shrink.fit(train,guard,'shrink'),'nb03':nb.fit(train,guard,'negative_binomial_alpha03')}
 if guard.seen!=set(core.unique(train)):raise ValueError('fit labels not fully consumed')
 expected=core.legacy.make_spec([core.normalize(r) for r in train]);expected_shrink=core.legacy.make_spec([shrink.transform(r,'shrink') for r in train])
 if models['baseline']['spec']!=expected or models['nb03']['spec']!=expected or models['shrink']['estimator']['spec']!=expected_shrink:raise ValueError('scaler must be training-only')
 return models

def validate(rows,ids):
 if len(rows)!=len(ids) or set(core.unique(rows))!=ids:raise ValueError('prediction cohort mismatch')
 for x in rows:
  v=x['probabilities_3way'];q=x['probabilities_2way']
  for z,ks in ((v,{'home','draw','away'}),(q,{'home','away'})):
   if set(z)!=ks or any(not math.isfinite(a) or not 0<=a<=1 for a in z.values()) or abs(sum(z.values())-1)>1e-9:raise ValueError('invalid probability')
  denom=v['home']+v['away']
  if denom<=0 or any(abs(q[k]-v[k]/denom)>1e-9 for k in ('home','away')):raise ValueError('conditional probability mismatch')

def ensemble(components):
 names=('baseline','shrink','nb03');ids=set(core.unique(components['baseline']));indexed={k:core.unique(components[k]) for k in names}
 if any(set(indexed[k])!=ids for k in names):raise ValueError('component population mismatch')
 out=[]
 for row in components['baseline']:
  fid=row['fixture_id'];src=[indexed[k][fid] for k in names]
  if any(x['event_date']!=row['event_date'] for x in src):raise ValueError('date mismatch')
  v={k:sum(x['probabilities_3way'][k] for x in src)/3 for k in ('home','draw','away')};denom=v['home']+v['away']
  out.append({'fixture_id':fid,'event_date':row['event_date'],'probabilities_3way':v,'probabilities_2way':{'home':v['home']/denom,'away':v['away']/denom}})
 return out

def run():
 train,target,labels=inputs();models=fit_components(train,labels)
 predictions={'baseline':core.predict_baseline(models['baseline'],target),'shrink':shrink.predict(models['shrink'],target),'nb03':nb.predict(models['nb03'],target)}
 predictions['equal_ensemble']=ensemble(predictions);ids=set(core.unique(target))
 for p in predictions.values():validate(p,ids)
 core.dump(HERE/'models2023.json',models);core.dump(HERE/'predictions2024.json',predictions)
 meta={'contract_sha256':CONTRACT_SHA,'old_contract_sha256':core.CONTRACT_SHA,'training_n':len(train),'target_n':len(target),'training_year':2023,'target_year':2024,'target_labels_or_prices_read':False,'training_label_poison_guard':'passed','training_only_scalers':'verified','not_fresh_holdout':True,'recipe':'equal mean of unconditional 3way then conditional 2way','sha256':{p:sha(HERE/p) for p in ('models2023.json','predictions2024.json')}}
 core.dump(HERE/'manifest.json',meta);print(json.dumps(meta,ensure_ascii=False))
if __name__=='__main__':run()
