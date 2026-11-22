"""Offline lagged, player-ID relief reuse proxy; never calls production."""
import gzip,hashlib,json,math,random
from collections import Counter,defaultdict
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit,logit
R=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
DATA=Path('C:/Users/user/Documents/Codex/kbtdata/experiments/kbo-actual-training-data-20261103')
MODEL=Path('C:/Users/user/Documents/Codex/kbtmodel/experiments/kbo-actual-training-20261103/artifacts')
OVER=R/'experiments/pregame-baseball-20261104/overlap.json'

def read_rows(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]
def build_events(rows):
 events=defaultdict(dict)
 for row in rows:
  day=date.fromisoformat(row['event_date'])
  for side in ('home','away'):
   team=row[side+'_id'];raw=row['raw_features'][side+'_bullpen_load'];v=raw.get('value') or {}
   for g in v.get('games',[]):
    past=date.fromisoformat(g['date']);assert past<day
    relief=g.get('relief') or {};pitch=relief.get('pitchers') or [];ids=[str(x['player_id']) for x in pitch if x.get('player_id') is not None]
    if not pitch or len(ids)!=len(pitch) or any(not x.isdigit() for x in ids) or len(set(ids))!=len(ids):continue
    if relief.get('appearances')!=len(pitch):continue
    key=(team,g['id']);variant=(g['date'],tuple(sorted(ids)));previous=events[key].get(variant)
    if previous is None or row['event_date']<previous:events[key][variant]=row['event_date']
 return dict(events),sum(len(variants)>1 for variants in events.values())

def proxy(events,team,target):
 d=date.fromisoformat(target);games=[]
 for (t,_),variants in events.items():
  if t!=team:continue
  known=[variant for variant,observed in variants.items() if date.fromisoformat(observed)<d]
  if len(known)!=1:continue
  day,ids=known[0]
  if 4<=(d-date.fromisoformat(day)).days<=18:games.append(ids)
 if len(games)<5:return None
 counts=Counter(pid for ids in games for pid in ids);return sum(n*(n-1) for n in counts.values())/sum(counts.values())

def fit(rows):
 r=[x for x in rows if x['winner']!='draw'];assert r
 def loss(b):
  z=np.array([logit(x['market'])+b*x['x'] for x in r]);y=np.array([x['winner']=='home' for x in r]);return float(np.mean(np.logaddexp(0,z)-y*z)+.1*b*b)
 z=minimize_scalar(loss,bounds=(-2,2),method='bounded');assert z.success;return float(z.x)

def augment(r,b):
 probs={'market':r['market'],'m0_projected':r['m0'],'candidate':float(expit(logit(r['market'])+b*r['x']))};arms={}
 for name,p in probs.items():
  side='home' if p>=.5 else 'away';odds=r['prices'][side];winner=r['winner'];arms[name]={'p_home':p,'side':side,'odds':odds,'profit':0 if winner=='draw' else odds-1 if side==winner else -1}
 return dict(r,arms=arms)
def score(rows,name):
 if not rows:return None
 ds=[r for r in rows if r['winner']!='draw'];p=lambda r:r['arms'][name]['p_home'];blocks=defaultdict(float)
 for r in rows:blocks[r['date']]+=r['arms'][name]['profit']
 cum=peak=dd=0
 for day in sorted(blocks):cum+=blocks[day];peak=max(peak,cum);dd=max(dd,peak-cum)
 hits=sum(r['arms'][name]['side']==r['winner'] for r in ds)
 return {'n':len(rows),'decided':len(ds),'refunds':len(rows)-len(ds),'hits':hits,'accuracy':hits/len(ds) if ds else None,'mean_odds':sum(r['arms'][name]['odds'] for r in rows)/len(rows),'roi':sum(r['arms'][name]['profit'] for r in rows)/len(rows),'ll':sum(-math.log(p(r) if r['winner']=='home' else 1-p(r)) for r in ds)/len(ds) if ds else None,'brier':sum((p(r)-(r['winner']=='home'))**2 for r in ds)/len(ds) if ds else None,'mdd':dd}
def ci(rows,a,b):
 by=defaultdict(list)
 for r in rows:by[r['date']].append(r)
 blocks=list(by.values());rng=random.Random(20261108);out=defaultdict(list)
 for _ in range(2000):
  sample=[r for _ in blocks for r in rng.choice(blocks)];x,y=score(sample,a),score(sample,b)
  for k in ('accuracy','roi','ll','brier'):
   if x[k] is not None:out[k].append(x[k]-y[k])
 return {k:[sorted(v)[int(.025*(len(v)-1))],sorted(v)[int(.975*(len(v)-1))]] for k,v in out.items()}
def main():
 paths=[DATA/'paired-train-projected.jsonl.gz',DATA/'paired-test-projected.jsonl.gz',MODEL/'development.json',MODEL/'predictions.json',OVER];source_hash={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rr=[r for p in paths[:2] for r in read_rows(p) if r['event_date'][:4] in ('2025','2026')];events,conflicts=build_events(rr)
 models={}
 for year,file in [('2025',paths[2]),('2026',paths[3])]:
  payload=json.loads(file.read_text(encoding='utf8'));pr=payload['predictions']['projected']['M0_projected']
  for x in pr:
   key=(year,x['fixture_id']);assert key not in models;models[key]=x['probabilities_2way']['home']
 overlap=json.loads(OVER.read_text(encoding='utf8'));quotes={}
 for year in ('2025','2026'):
  for p in overlap[year]['rows']:
   key=(year,p['fixture_id']);assert key not in quotes;quotes[key]=p
 rows=[];counts=defaultdict(Counter)
 for r in rr:
  year=r['event_date'][:4];counts[year]['paired_fixture_opportunities']+=1;h=proxy(events,r['home_id'],r['event_date']);a=proxy(events,r['away_id'],r['event_date'])
  if h is None or a is None:counts[year]['insufficient_team_history']+=1;continue
  counts[year]['history_proxy_available']+=1;key=(year,r['fixture_id'])
  if key not in models:counts[year]['missing_m0_projected']+=1;continue
  if key not in quotes:counts[year]['missing_exact_price_outcome']+=1;continue
  q=quotes[key];ph=float(q['prices']['home']);pa=float(q['prices']['away']);market=(1/ph)/(1/ph+1/pa)
  rows.append({'fixture_id':r['fixture_id'],'date':r['event_date'],'year':year,'home':r['home_id'],'away':r['away_id'],'x':a-h,'market':market,'m0':models[key],'prices':q['prices'],'market_id':q['market_id'],'winner':q['winner']});counts[year]['matched']+=1
 train=[r for r in rows if r['year']=='2025' and r['date']<='2025-06-30'];val=[r for r in rows if r['year']=='2025' and r['date']>'2025-06-30'];test=[r for r in rows if r['year']=='2026'];assert min(len(train),len(val))>=30 and test
 bv=fit(train);b=fit(train+val);validation=[augment(r,bv) for r in val];evaluation=sorted((augment(r,b) for r in test),key=lambda r:(r['date'],r['fixture_id']));names=('market','m0_projected','candidate')
 summary={'source_sha256':source_hash,'counts':{k:dict(v) for k,v in counts.items()},'history_team_game_keys':len(events),'conflicting_history_keys_in_full_archive':conflicts,'split_n':{'fit':len(train),'validation':len(val),'test':len(test)},'date_bounds':{k:[min(x['date'] for x in v),max(x['date'] for x in v)] for k,v in [('fit',train),('validation',val),('test',test)]},'beta_validation':bv,'beta_frozen':b,'validation':{k:score(validation,k) for k in names},'test':{k:score(evaluation,k) for k in names},'changed_vs_market':sum(r['arms']['candidate']['side']!=r['arms']['market']['side'] for r in evaluation),'changed_vs_m0_projected':sum(r['arms']['candidate']['side']!=r['arms']['m0_projected']['side'] for r in evaluation),'paired_ci':{k:ci(evaluation,'candidate',k) for k in ('market','m0_projected')},'by_month':{month:{k:score([r for r in evaluation if r['date'][:7]==month],k) for k in names} for month in sorted({r['date'][:7] for r in evaluation})},'limits':['retrospective_source_unverified: historical receipt/price time unavailable','prior game observations use first archive witness strictly before target and dates lagged four days; completion times unverified','schedule coverage unverified, 5 observed games is not completeness proof','2025 price cohort excludes draws due to outcome-dependent matching','M0 projected roster generated from previous games, historical receipt unverified and not production model','prior observed 2026 data: not blind','single market only, no prospectively observed player availability']}
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');
 with (OUT/'predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(filename='',fileobj=f,mode='wb',mtime=0) as g:
   for row in validation+evaluation:g.write((json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode())
 print(json.dumps({k:summary[k] for k in ('counts','history_team_game_keys','conflicting_history_keys_in_full_archive','split_n','date_bounds','beta_frozen','validation','test','changed_vs_market','paired_ci')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
