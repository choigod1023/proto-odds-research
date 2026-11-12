import gzip, json, math, hashlib, random, statistics
from collections import defaultdict, Counter
from datetime import date
from pathlib import Path
R=Path(__file__).resolve().parents[2]; D=Path(__file__).resolve().parent

def logit(p):
 p=max(1e-9,min(1-1e-9,p)); return math.log(p/(1-p))
def sigmoid(x): return 1/(1+math.exp(-x))
def profit(side,winner,odds): return 0.0 if winner=='draw' else (odds[side]-1 if side==winner else -1.0)
def metrics(rows,arm):
 if not rows: return None
 decided=[r for r in rows if r['winner']!='draw']; pp=[r['arms'][arm] for r in rows]; wins=sum(x['side']==r['winner'] for x,r in zip(pp,rows) if r['winner']!='draw'); pn=[x['profit'] for x in pp]; cum=peak=dd=0
 for x in pn:
  cum+=x;peak=max(peak,cum);dd=max(dd,peak-cum)
 return {'n':len(rows),'decided':len(decided),'refunds':len(rows)-len(decided),'coverage':1.0,'hits':wins,'accuracy':wins/len(decided) if decided else None,'mean_odds':statistics.mean(x['odds'] for x in pp),'roi':sum(pn)/len(rows),'logloss':statistics.mean(-math.log(max(1e-12,r['arms'][arm]['p_home'] if r['winner']=='home' else 1-r['arms'][arm]['p_home'])) for r in decided) if decided else None,'brier':statistics.mean((r['arms'][arm]['p_home']-(r['winner']=='home'))**2 for r in decided) if decided else None,'mdd_units':dd}
def ci(rows, arm, base):
 groups=defaultdict(list)
 for r in rows: groups[date.fromisoformat(r['date']).isocalendar()[:2]].append(r)
 blocks=list(groups.values()); rng=random.Random(20261108); vals={'roi':[],'logloss':[]}
 for _ in range(2000):
  b=[r for _ in blocks for r in rng.choice(blocks)]; vals['roi'].append(sum(r['arms'][arm]['profit']-r['arms'][base]['profit'] for r in b)/len(b)); q=[r for r in b if r['winner']!='draw']
  if q: vals['logloss'].append(sum(-math.log(r['arms'][arm]['p_home'] if r['winner']=='home' else 1-r['arms'][arm]['p_home'])+math.log(r['arms'][base]['p_home'] if r['winner']=='home' else 1-r['arms'][base]['p_home']) for r in q)/len(q))
 return {k:[sorted(v)[int(.025*(len(v)-1))],sorted(v)[int(.975*(len(v)-1))]] for k,v in vals.items()}
def main():
 inp=R/'experiments/pregame-baseball-20261104/overlap.json'; mp=R/'experiments/pregame-baseball-20261104/model-predictions.jsonl.gz'; overlap=json.loads(inp.read_text(encoding='utf8')); models={}
 with gzip.open(mp,'rt',encoding='utf8') as f:
  for line in f:
   r=json.loads(line); p=r['payload']; mid=p['model_id']
   if mid in ('kbo-actual-training-2025-M0_actual','kbo-actual-training-2026-M0_actual'):
    key=(mid[20:24],p['fixture_id']); assert key not in models; models[key]=p['probabilities']['home']
 rows=[]; counts={}
 for year in ('2025','2026'):
  entries=overlap[year]['rows']; counts[year]={'overlap':len(entries),'matched':0}
  for e in entries:
   key=(year,e['fixture_id'])
   if key not in models: continue
   p=models[key]; prices=e['prices']; pm=(1/prices['home'])/(1/prices['home']+1/prices['away']); ds=e['fixture_id'][:8]; dt=f'{ds[:4]}-{ds[4:6]}-{ds[6:8]}'
   rows.append({'fixture_id':e['fixture_id'],'date':dt,'year':year,'league':'KBO','market_id':e['market_id'],'prices':prices,'winner':e['winner'],'m0':p,'market':pm});counts[year]['matched']+=1
 train=[r for r in rows if r['year']=='2025' and r['winner']!='draw']; assert train
 def loss(t): return sum(-math.log(sigmoid(logit(r['m0'])*t) if r['winner']=='home' else 1-sigmoid(logit(r['m0'])*t)) for r in train)/len(train)
 scores={str(t):loss(t) for t in (.75,1.,1.25)}; t=min((.75,1.,1.25),key=lambda x:(scores[str(x)],abs(x-1)))
 for r in rows:
  m=r['m0']; q=r['market']; probs={'market':q,'m0':m,'temperature':sigmoid(logit(m)*t),'equal_logit_pool':sigmoid((logit(m)+logit(q))/2)};r['arms']={}
  for a,p in probs.items():
   side='home' if p>=.5 else 'away'; r['arms'][a]={'p_home':p,'side':side,'odds':r['prices'][side],'profit':profit(side,r['winner'],r['prices'])}
 rows.sort(key=lambda x:(x['date'],x['fixture_id'])); test=[r for r in rows if r['year']=='2026']; arms=('market','m0','temperature','equal_logit_pool');
 out={'input_sha256':{str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (inp,mp)},'counts':counts,'validation_logloss':scores,'temperature':t,'metrics_2026':{a:metrics(test,a) for a in arms},'paired_week_block_ci_2026':{a:ci(test,a,'m0') for a in ('temperature','equal_logit_pool')},'by_month':{},'by_odds_band':{},'limitations':['retrospective only; no verified timely lineup or price receipt','2025 source excludes all draws through outcome-dependent matching','2026 actual M0 export subset; not an independent sample or current operating model','no eligible MLB or NPB matched player and settlement evidence established','prices not confirmed executable','same-day ordering for drawdown uses fixture ID rather than settlement timestamp']}
 for month in sorted({r['date'][:7] for r in test}): out['by_month'][month]={a:metrics([r for r in test if r['date'].startswith(month)],a) for a in arms}
 # Bands use baseline M0 odds for a shared, fixed comparison population.
 for name,lo,hi in [('1.00-1.49',1,1.5),('1.50-1.79',1.5,1.8),('1.80+',1.8,float('inf'))]: out['by_odds_band'][name]={a:metrics([r for r in test if lo<=r['arms']['m0']['odds']<hi],a) for a in arms}
 (D/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 with gzip.GzipFile(filename='',mode='wb',fileobj=(D/'model-predictions.jsonl.gz').open('wb'),mtime=0) as f:
  for r in rows:f.write((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode())
 print(json.dumps({'counts':counts,'validation':scores,'temperature':t,'metrics_2026':out['metrics_2026'],'ci':out['paired_week_block_ci_2026']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
