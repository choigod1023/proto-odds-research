import gzip, json, hashlib, math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

BASE=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/all-sports-underdog-validation-20261031/experiments/all-sports-underdog-20261031')
OUT=Path(__file__).resolve().parent
START={'common.py':'2a507d75338442fee edb1d7ec85402f0f3f8217758b7ac8f89ad7b9a21c8e092'.replace(' ',''),'protocol.json':'b6397461d8144a802ce80be25be45ccdbb8f9b9fbdf3f89079759b8afca22590','inputs/domestic.jsonl.gz':'4bf3abf8bb82838ee72975e1ef486a471b357a03ee4d8627808780f046dae51a','inputs/external-football.jsonl.gz':'31c4e7bc3a4dfc4bafa1bcb70ba295e99d910b3e326c14bfffe5bb0bf1613605'}
models=['market','outcome_form','market_residual','half_market_form']
policies=['all','favorites','underdogs','market_same_underdogs']
def read(path):
 with gzip.open(path,'rt',encoding='utf8') as f:
  for line in f: yield json.loads(line,parse_float=Decimal)
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def empty():return {'bets':0,'hits':0,'net':Decimal(0)}
def result(a):return {'bets':a['bets'],'hits':a['hits'],'net_units':str(a['net']),'roi':float(a['net']/a['bets']) if a['bets'] else None}
inputs={}
for file in ('inputs/domestic.jsonl.gz','inputs/external-football.jsonl.gz'):
 for r in read(BASE/file):
  if r['sport']=='sc':
   key=(r['cohort'],r['id']); assert key not in inputs,('duplicate input',key); inputs[key]=r
validation={'method':'Independent Decimal recalculation from input odds and label; argmax, normalized market odds, groups, rank, draw, hit and net checked per ledger prediction; full and yearly four-policy aggregates compared to results.json.', 'input_sha256_start':START,'cohorts':{}}
for folder in sorted(p for p in OUT.iterdir() if p.is_dir()):
 report=json.loads((folder/'results.json').read_text(encoding='utf8'))
 aggregates=defaultdict(empty); seen=set(); fall=0; dates=[]
 for r in read(folder/'ledger.jsonl.gz'):
  key=(folder.name,r['id']); assert key in inputs and key not in seen,('missing or duplicate',key); seen.add(key); src=inputs[key]
  for field in ('odds','y','day','kickoff','sport','cohort','league','home','away'): assert r[field]==src[field],(key,field,r[field],src[field])
  assert 0 <= src['y'] < 3 and len(src['odds'])==3 and all(o>1 for o in src['odds']),key
  inv=[Decimal(1)/o for o in src['odds']]; q=[v/sum(inv) for v in inv]; actualq=r['market_p']
  assert all(abs(a-b)<Decimal('1e-12') for a,b in zip(q,actualq)),(key,'market_p')
  fall+=bool(r['fallback']); dates.append(r['day'])
  preds=r['predictions']; assert set(preds)==set(models),key
  for m in models:
   p=preds[m]; prob=p['p']; assert len(prob)==3 and abs(sum(prob)-1)<Decimal('1e-10'),(key,m,'p')
   pick=max(range(3),key=lambda i:prob[i]); assert p['pick']==pick,(key,m,'argmax')
   top=max(q); group='underdog' if q[pick]<top-Decimal('1e-12') else 'top_tie' if sum(abs(v-top)<=Decimal('1e-12') for v in q)>1 else 'favorite'
   rank=1+sum(v>q[pick]+Decimal('1e-12') for v in q); hit=pick==src['y']; net=src['odds'][pick]-1 if hit else Decimal(-1)
   assert (p['group'],p['rank'],p['draw'],p['hit'],Decimal(p['net']))==(group,rank,pick==1,hit,net),(key,m,'classification/payout')
   for policy in policies:
    if not (policy=='all' or policy=='favorites' and group=='favorite' or policy in ('underdogs','market_same_underdogs') and group=='underdog'):continue
    chosen=preds['market'] if policy=='market_same_underdogs' else p; cp=chosen['pick']; ch=cp==src['y']; cn=src['odds'][cp]-1 if ch else Decimal(-1)
    for year in ('full',r['day'][:4]):
     a=aggregates[(year,m,policy)];a['bets']+=1;a['hits']+=int(ch);a['net']+=cn
 expected={k for k in inputs if k[0]==folder.name}; assert seen==expected,(folder.name,'coverage',len(seen),len(expected))
 assert len(seen)==report['count'] and fall==report['fallback_events'] and [min(dates),max(dates)]==report['range'],folder.name
 for period,section in [('full',report['full'])]+list(report['by_year'].items()):
  for m in models:
   for policy in policies:
    x=result(aggregates[(period,m,policy)]); y=section[m]['policies'][policy]
    assert x['bets']==y['bets'] and x['hits']==y['hits'] and Decimal(x['net_units'])==Decimal(y['net_units']) and (x['roi']==y['roi'] or x['roi'] is not None and y['roi'] is not None and abs(x['roi']-y['roi'])<1e-12),(folder.name,period,m,policy,x,y)
 states=json.loads((folder/'states.json').read_text(encoding='utf8'))
 annual={m:{year:{p:result(aggregates[(year,m,p)]) for p in policies} for year in sorted(report['by_year'])} for m in models}
 validation['cohorts'][folder.name]={'count':len(seen),'range':report['range'],'fallback_events':fall,'fallback_months':[s['month'] for s in states if s['fallback']],'full':{m:{p:result(aggregates[('full',m,p)]) for p in policies} for m in models},'by_year':annual,'yearly_underdog_signs':{m:{year:('positive' if Decimal(annual[m][year]['underdogs']['net_units'])>0 else 'negative' if Decimal(annual[m][year]['underdogs']['net_units'])<0 else 'zero') for year in annual[m]} for m in models}}
 print(folder.name,len(seen),'verified',flush=True)
assert set(validation['cohorts'])=={k[0] for k in inputs},'cohort coverage'
validation['input_sha256_end']={p:sha(BASE/p) for p in START}; assert validation['input_sha256_end']==START,('input hash changed',validation['input_sha256_end'])
validation['status']='passed'
(OUT/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
