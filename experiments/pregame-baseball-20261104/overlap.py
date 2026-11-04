"""Recompute exact-ID retrospective overlaps from already frozen local artifacts."""
import gzip, hashlib, json
from pathlib import Path
from collections import Counter
R=Path('C:/Users/user/Documents/Codex'); ROOT=Path(__file__).resolve().parents[2]
def load(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]
def index(rows):
 d={}
 for row in rows:
  k=row['fixture_id']
  if k in d: raise ValueError('duplicate fixture '+k)
  d[k]=row
 return d
model=R/'kbtmodel/experiments/kbo-actual-training-20261103/artifacts/development.json'
effects=R/'kbeffects/experiments/kbo-player-effects-20261103/artifacts/predictions.json'
prices26=R/'kbeffects/experiments/kbo-player-effects-20261103/inputs/price-ledgers.json'
votes=R/'vote-accuracy/experiments/vote-accuracy-same-period-20261104/inputs.json'
prices25=ROOT/'experiments/kbo-m0-followup-20261103/data/prices-2025.jsonl.gz'
labels25=ROOT/'experiments/kbo-m0-followup-20261103/data/labels-2025.jsonl.gz'
a=load(model); b=load(effects); vote=load(votes); prices=index(lines(prices25)); labels=index(lines(labels25))
models25={k:index(v) for k,v in a['predictions']['actual'].items() if k in ('M0_actual','M2_actual')}
ids25=set(prices)&set(labels)
for d in models25.values():ids25 &= set(d)
models26={k:index(v) for k,v in b['scenarios']['official_batting_and_starter'].items()}
ledger=index(load(prices26)['ledgers']['extended_day_team_source_confirmed']); ids26=set(ledger)
for d in models26.values():ids26 &= set(d)
def summarize(ids,models,price_get,winner_get):
 out=[];scores={k:0 for k in ['market',*models]}
 for id in sorted(ids):
  price=price_get(id); winner=winner_get(id); picks={k:max(('home','away'),key=lambda s:m[id]['probabilities_2way'][s]) for k,m in models.items()};picks['market']=max(('home','away'),key=lambda s:1/float(price[s]));
  for k,p in picks.items():scores[k]+=0 if winner=='draw' else (float(price[p])-1 if winner==p else -1)
  out.append({'fixture_id':id,'market_id':price_get(id,True),'prices':price,'winner':winner,'picks':picks})
 return out,{k:round(v/len(ids),6) for k,v in scores.items()} if ids else {}
def p25(i,market=False):return prices[i]['price_source']['market_id'] if market else prices[i]['price_source']['prices']
def w25(i):
 r=labels[i];return 'home' if r['home_score']>r['away_score'] else 'away' if r['away_score']>r['home_score'] else 'draw'
def p26(i,market=False):return ledger[i]['price_source']['market_id'] if market else ledger[i]['price_source']['prices']
def w26(i):return ledger[i]['winner']
r25,s25=summarize(ids25,models25,p25,w25);r26,s26=summarize(ids26,models26,p26,w26)
bs=[v for v in vote if v.get('sport')=='bs']; official_vote_ids={v.get('fixture',{}).get('source_event_id') for v in bs if v.get('fixture',{}).get('source_event_id')}
result={'sources':{str(p):sha(p) for p in [model,effects,prices25,labels25,prices26,votes]},'method':'exact fixture_id only; no fuzzy match; raw vote inputs examined are limited to specified 14 snapshot artifact','2025':{'models':{k:len(v) for k,v in models25.items()},'train_seasons':[2023,2024],'validation_start':'2025-03-22','M1':'not present in matched actual-roster development artifact','intersection_count':len(r25),'roi':s25,'rows':r25},'2026':{'models':{k:len(v) for k,v in models26.items()},'train_seasons':[2023,2024,2025],'intersection_count':len(r26),'roi':s26,'rows':r26},'votes':{'snapshot_total':len(vote),'baseball_count':len(bs),'baseball_source_event_ids':sorted(official_vote_ids),'exact_id_intersections_2025':sorted(ids25 & official_vote_ids),'exact_id_intersections_2026':sorted(ids26 & official_vote_ids)},'limitations':['retrospective lineup and price; historical receipts unverified','prices not independently proven purchasable or official Proto offer','vote artifact only; missing ID is unlinked, not proof of no votes elsewhere','2025 and 2026 models are distinct research runs','ROI single unit draw refund, not requested wallet policy']}
out=Path(__file__).with_name('overlap.json');out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({y:{'count':result[y]['intersection_count'],'roi':result[y]['roi']} for y in ['2025','2026']}|{'votes':result['votes']},ensure_ascii=False))
