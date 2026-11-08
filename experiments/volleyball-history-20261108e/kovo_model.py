import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[k]='1'
import collections,hashlib,json,gzip,sys
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
from scipy.special import expit,logit
from scipy.optimize import minimize_scalar
from kovo_parse import parse
# Load the sibling by path: other experiments also expose a module named 'run'.
import importlib.util
_spec = importlib.util.spec_from_file_location('volleyball_history_metrics_20261108e', Path(__file__).resolve().with_name('run.py'))
_metrics_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metrics_module)
metrics = _metrics_module.metrics
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
ALIASES={'흥국생명':'흥국생명','정관장':'대전정관','GS칼텍스':'GS칼텍스','IBK기업은행':'IBK기업','페퍼저축은행':'페퍼저축','한국도로공사':'도로공사','현대건설':'현대건설','한국전력':'한국전력','우리카드':'우리카드','삼성화재':'삼성화재','OK저축은행':'OK저축','현대캐피탈':'현대캐피','KB손해보험':'KB손보','대한항공':'대한항공'}
def build_features(games,prices):
 bykey=collections.defaultdict(list)
 for g in games:bykey[(g['league'],g['date'],g['home'],g['away'])].append(g)
 out=[];audit=collections.Counter();price_keys=collections.Counter((p['league'],p['day'],p['home'],p['away']) for p in prices)
 for p in prices:
  key=(p['league'],p['day'],p['home'],p['away']);audit[p['league']+'/'+p['day'][:4]+'/prices']+=1;found=bykey.get(key,[])
  if price_keys[key]!=1:audit[p['league']+'/'+p['day'][:4]+'/duplicate_price_fixture']+=1;continue
  if len(found)!=1:audit[p['league']+'/'+p['day'][:4]+'/no_unique_result']+=1;continue
  g=found[0]
  if (g['sets'][0]>g['sets'][1])!=(p['y']==0):audit[p['league']+'/'+p['day'][:4]+'/result_conflict']+=1;continue
  date=datetime.fromisoformat(p['day']);stats=[];history_ids=[]
  for team in [p['home'],p['away']]:
   prior=sorted([x for x in games if x['league']==p['league'] and team in (x['home'],x['away']) and datetime.fromisoformat(x['date'])<=date-timedelta(days=4)],key=lambda x:(x['date'],x['number']))[-10:];
   if len(prior)<5:break
   vals=[x['receive'][0 if x['home']==team else 1] for x in prior];
   if any(v is None for v in vals):break
   stats.append(sum(vals)/len(vals));history_ids.append([x['number'] for x in prior])
  if len(stats)!=2:audit[p['league']+'/'+p['day'][:4]+'/insufficient_history']+=1;continue
  inv=[1/o for o in p['odds']];out.append({'id':p['id'],'date':p['day'],'league':p['league'],'y':int(p['y']==0),'odds':p['odds'],'market':inv[0]/sum(inv),'feature':(stats[0]-stats[1])/100,'official_number':g['number'],'history_game_numbers':history_ids});audit[p['league']+'/'+p['day'][:4]+'/eligible']+=1
 return out,dict(audit)
def fit(rows):
 raw=np.array([r['feature'] for r in rows]);sigma=float(raw.std());
 if sigma<=1e-12: raise ValueError('zero_feature_scale')
 x=raw/sigma;y=np.array([r['y'] for r in rows]);z=logit([r['market'] for r in rows]);return float(minimize_scalar(lambda b:float(np.mean(np.logaddexp(0,z+b*x)-y*(z+b*x))+b*b),bounds=(-2,2),method='bounded').x),sigma
def main(root):
 root=Path(root);raw=(root/'api2.body').read_bytes();schedule=json.loads(raw)['payload'];
 if len({(r['season']['value'],r['league']['value'],r['number']) for r in schedule})!=len(schedule):raise ValueError('duplicate_official_game_id')
 manifest=json.loads((root/'results/manifest.json').read_text(encoding='utf8'));games=[];ex=collections.Counter()
 for row in schedule:
  item=next((x for x in manifest.values() if x.get('status')=='ok' and x.get('number')==row['number']),None)
  if not item:ex['no_record']+=1;continue
  pid=item['url'].split('PID=')[1];body=(root/'results'/f'{pid}.html').read_bytes()
  if hashlib.sha256(body).hexdigest()!=item['sha256']:raise ValueError('hash mismatch')
  v=parse(body,item['url']);names=[row['homeTeam']['name'],row['awayTeam']['name']];
  if v['teams']!=names or v['date']!=row['gameDate'] or v['sets_won']!=[row['homeTeam']['score'],row['awayTeam']['score']] or v['local_start']!=row['startTime'][:5]:ex['fixture_mismatch']+=1;continue
  try:
   home,away=[ALIASES[x] for x in names];receive=[float(x['receive_efficiency_pct']) if x['receive_efficiency_pct'] else None for x in v['team_totals']]
  except (KeyError,ValueError):ex['missing_identity_or_receive']+=1;continue
  games.append({'league':'KOVO남' if row['gender']=='MALE' else 'KOVO여','home':home,'away':away,'date':row['gameDate'],'sets':v['sets_won'],'receive':receive,'number':row['number']})
 source=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz';prices=[json.loads(x) for x in gzip.open(source,'rt',encoding='utf8')];prices=[x for x in prices if x['league'] in ('KOVO남','KOVO여') and '2025-10-18'<=x['day']<='2026-03-19'];rows,audit=build_features(games,prices);output=[];summary={'schedule_sha256':hashlib.sha256(raw).hexdigest(),'price_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'schedule_count':len(schedule),'collected_count':len(games),'exclusions':dict(ex),'join_audit':audit,'groups':{}}
 for league in ('KOVO남','KOVO여'):
  allr=sorted([x for x in rows if x['league']==league],key=lambda x:(x['date'],x['id']));yr=[x for x in allr if x['date']<'2026'];test=[x for x in allr if x['date']>='2026'];cut=int(len(yr)*.8);boundary=yr[cut-1]['date'] if cut else '';val=[x for x in yr if x['date']>boundary];val_cutoff=(datetime.fromisoformat(val[0]['date'])-timedelta(days=4)).date().isoformat() if val else '';train=[x for x in yr if x['date']<=val_cutoff];
  if len(train)<15 or not val or not test:summary['groups'][league]={'train':len(train),'validation':len(val),'test':len(test),'status':'insufficient'};continue
  holdout_cutoff=(datetime.fromisoformat(test[0]['date'])-timedelta(days=4)).date().isoformat();final_train=[x for x in yr if x['date']<=holdout_cutoff];
  if len(final_train)<15:summary['groups'][league]={'status':'insufficient_final_label_history'};continue
  validation_beta,validation_sigma=fit(train);vm=np.array([r['market'] for r in val]);vp=expit(logit(vm)+validation_beta*np.array([r['feature'] for r in val])/validation_sigma);beta,sigma=fit(final_train);m=np.array([x['market'] for x in test]);p=expit(logit(m)+beta*np.array([x['feature'] for x in test])/sigma);days=sorted(set(x['date'] for x in test));rng=np.random.default_rng(20261108);diff={k:[] for k in ('accuracy','roi','log_loss','brier')}
  for _ in range(2000):
   sample=rng.choice(days,len(days),replace=True);ii=[i for day in sample for i,x in enumerate(test) if x['date']==day];rr=[test[i] for i in ii];b=metrics(rr,m[ii]);c=metrics(rr,p[ii]);
   for k in diff:diff[k].append(c[k]-b[k])
  summary['groups'][league]={'train':len(train),'validation':len(val),'test':len(test),'2026_price_rows':audit.get(league+'/2026/prices',0),'coverage':len(test)/audit[league+'/2026/prices'],'beta':beta,'feature_sigma':sigma,'final_training_n':len(final_train),'final_label_cutoff':holdout_cutoff,'validation_label_cutoff':val_cutoff,'validation_market':metrics(val,vm),'validation_candidate':metrics(val,vp),'max_abs_probability_change':float(np.max(np.abs(p-m))),'market':metrics(test,m),'candidate':metrics(test,p),'changed_picks':int(sum((m>=.5)!=(p>=.5))),'paired_95':{k:list(map(float,np.quantile(v,[.025,.975]))) for k,v in diff.items()},'monthly':{month:{'market':metrics([r for r in test if r['date'].startswith(month)],m[[i for i,r in enumerate(test) if r['date'].startswith(month)]]),'candidate':metrics([r for r in test if r['date'].startswith(month)],p[[i for i,r in enumerate(test) if r['date'].startswith(month)]])} for month in sorted(set(r['date'][:7] for r in test))}}
  for r,prob in zip(test,p):output.append(r|{'candidate':float(prob)})
 (HERE/'kovo-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');
 with (HERE/'kovo-predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as z:
   for r in output:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode())
if __name__=='__main__':main(sys.argv[1])
