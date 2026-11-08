import os
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']: os.environ[k]='1'
import gzip,json,hashlib,collections,math,datetime
from pathlib import Path
import numpy as np
from scipy.stats import poisson
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import Ridge
P=Path(__file__).resolve().parent
ROOT=P.parents[1]
def readgz(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(s) for s in f]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def writegz(p,rows):
 with open(p,'wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as z:
   for r in rows:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())
def compatible_price(row, score, ledger):
 ps=ledger.get('price_source') or {}; prices=ps.get('prices') or {}
 winner='home' if score['hg']>score['ag'] else 'away' if score['hg']<score['ag'] else 'draw'
 return (row['fixture_id']==score.get('source_fixture_id')==ledger.get('fixture_id')
         and winner==ledger.get('winner') and ledger.get('home_id')==row['home_id']
         and ledger.get('away_id')==row['away_id'] and bool(ps.get('market_id'))
         and all(isinstance(prices.get(k),(int,float)) and math.isfinite(prices[k]) and prices[k]>1 for k in ('home','away')))
def extract():
 paths={'rows':Path('C:/Users/user/Documents/Codex/kbeffects/experiments/kbo-player-effects-20261103/inputs/rows.jsonl.gz'),'official_lineups':Path('C:/Users/user/Documents/Codex/kbeffects/experiments/kbo-player-effects-20261103/inputs/fresh-history-2026-official_batting_and_starter.jsonl.gz'),'scores':Path('C:/Users/user/Documents/Codex/kbodata/data/model_candidates/kbo/archived-games.jsonl.gz'),'prices':Path('C:/Users/user/Documents/Codex/kbeffects/experiments/kbo-player-effects-20261103/inputs/price-ledgers.json'),'existing':ROOT/'experiments/pregame-baseball-20261104/model-predictions.jsonl.gz'}
 rows=readgz(paths['rows']); scores=readgz(paths['scores']); groups=collections.defaultdict(list)
 for x in scores:groups[x.get('source_fixture_id')].append(x)
 led=json.loads(paths['prices'].read_text(encoding='utf8'))['ledgers']['extended_day_team_source_confirmed']; ledger={x['fixture_id']:x for x in led}
 existing={}
 for x in readgz(paths['existing']):
  q=x['payload']
  if q['model_id']=='kbo-player-effects-v2-2026-M0':existing[q['fixture_id']]=q['probabilities']['home']
 official={x['fixture_id']:x for x in readgz(paths['official_lineups'])};out=[];reasons=collections.Counter()
 for r in rows:
  if r['season'] not in (2025,2026):continue
  fid=r['fixture_id']; s=groups.get(fid,[]); f=r['features']; h=f['home_actual_lineup_assumed']['value']; a=f['away_actual_lineup_assumed']['value']
  if r['season']==2026:
   o=official.get(fid); ctx=o.get('official_lineup_context') if o else None
   if ctx and ctx.get('lineups'):
    def ordered(side):
     z=ctx['lineups'].get(side,[]); z=sorted(z,key=lambda x:x.get('order',0));return [x.get('player_id') for x in z] if [x.get('order') for x in z]==list(range(1,10)) else []
    h,a=ordered('home'),ordered('away')
   else:h,a=[],[]
  if len(s)!=1:reasons['score_missing_or_duplicate']+=1;continue
  if not isinstance(h,list) or not isinstance(a,list) or len(h)!=9 or len(a)!=9 or len(set(h))!=9 or len(set(a))!=9 or any(not z for z in h+a):reasons['invalid_lineup']+=1;continue
  v=s[0]; rec={'id':fid,'date':v['date'],'home':r['home_id'],'away':r['away_id'],'home_lineup':h,'away_lineup':a,'home_runs':v['hg'],'away_runs':v['ag'],'price':None,'existing_p':None}
  if fid in ledger:
   z=ledger[fid]; ps=z['price_source']; winner='home' if v['hg']>v['ag'] else 'away' if v['hg']<v['ag'] else 'draw'
   if compatible_price(r,v,z):
    rec['price']={'id':ps['market_id'],'home':ps['prices']['home'],'away':ps['prices']['away'],'winner':winner}; rec['existing_p']=existing.get(fid)
   else:reasons['price_identity_mismatch']+=1
  out.append(rec)
 out.sort(key=lambda x:(x['date'],x['id']))
 writegz(P/'inputs.jsonl.gz',out)
 provenance=[]
 for r in out:
  o=official.get(r['id']);ctx=o.get('official_lineup_context') if o else None
  if ctx and ctx.get('lineup_evidence'):provenance.append({'id':r['id'],'lineup_evidence':ctx['lineup_evidence'],'historical_publication_time':ctx.get('historical_publication_time'),'historical_receipt_proven':ctx.get('historical_receipt_proven')})
 writegz(P/'official-lineup-provenance.jsonl.gz',provenance)
 manifest={'sources':{k:{'path':str(v),'sha256':sha(v)} for k,v in paths.items()},'output_sha256':sha(P/'inputs.jsonl.gz'),'source_row_counts':dict(collections.Counter(str(x['season']) for x in rows)),'exclusions':dict(reasons),'extracted_by_year':dict(collections.Counter(x['date'][:4] for x in out)),'price_by_year':dict(collections.Counter(x['date'][:4] for x in out if x['price'])),'draws_by_year':dict(collections.Counter(x['date'][:4] for x in out if x['home_runs']==x['away_runs'])),'available_at':None,'timing':'retrospective; first observation unknown','warning':'Lineup is postgame inferred; archived data not independently verified official; price times and execution unverified'}
 (P/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
def features(r,side,pair,penalty):
 home=side=='home'; ids=r[side+'_lineup']; opponent=r['away' if home else 'home']
 d={'home':int(home),'team='+r[side]:1,'opp='+opponent:1}
 for i,p in enumerate(ids):d['batter='+p]=(4.7-i*.1)/4.3
 if pair:
  for i,p in enumerate(ids):d['pair='+p+'>'+ids[(i+1)%9]]=math.sqrt(100/penalty)
 return d
def fit(rows,pair,penalty):
 ds=[];ys=[]
 for r in rows:
  for side in ['home','away']:ds.append(features(r,side,pair,penalty));ys.append(r[side+'_runs'])
 v=DictVectorizer();X=v.fit_transform(ds);m=Ridge(alpha=100,solver='lsqr',tol=1e-8);m.fit(X,ys);return v,m
def predict(model,r,pair,penalty):
 v,m=model;return np.clip(m.predict(v.transform([features(r,s,pair,penalty) for s in ['home','away']])),0.05,25)
def hp(lam):
 h,a=(poisson.pmf(np.arange(31),v) for v in lam);home=float(np.tril(np.outer(h,a),-1).sum());away=float(np.triu(np.outer(h,a),1).sum());return home/(home+away)
def run():
 rows=readgz(P/'inputs.jsonl.gz'); train=[r for r in rows if r['date']<'2025-09-01'];valid=[r for r in rows if '2025-09-01'<=r['date']<'2026-01-01'];test=[r for r in rows if r['date']>='2026-01-01']; alltrain=train+valid
 vals={}
 for penalty in (10,100,1000):
  model=fit(train,True,penalty);vals[str(penalty)]=float(np.mean([(float(p)-y)**2 for r in valid for p,y in zip(predict(model,r,True,penalty),[r['home_runs'],r['away_runs']])]))
 pen=min((10,100,1000),key=lambda x:(vals[str(x)],-x)); base=fit(alltrain,False,100); pair=fit(alltrain,True,pen);pred=[]
 for r in test:
  b=predict(base,r,False,100);c=predict(pair,r,True,pen)
  pred.append({'id':r['id'],'date':r['date'],'home_runs':r['home_runs'],'away_runs':r['away_runs'],'price':r['price'],'existing_p':r['existing_p'],'additive_runs':b.tolist(),'pair_runs':c.tolist(),'additive_p':hp(b),'pair_p':hp(c)})
 writegz(P/'predictions.jsonl.gz',pred)
 # only exact matching score, market and existing model; retain draws if present
 common=[x for x in pred if x['price'] and x['existing_p'] is not None];
 def vals_for(x,name):
  p=x['price']; ph={'additive':x['additive_p'],'pair':x['pair_p'],'existing':x['existing_p'],'market':(1/p['home'])/(1/p['home']+1/p['away'])}[name];pick='home' if ph>=.5 else 'away'; win=p['winner']; hit=None if win=='draw' else int(win==pick);net=0 if hit is None else p[pick]-1 if hit else -1
  return ph,pick,hit,net
 def metrics(xs,name):
  data=[vals_for(x,name) for x in xs]; nd=[(x,z) for x,z in zip(xs,data) if z[2] is not None]; equity=peak=mdd=0
  for z in data:equity+=z[3];peak=max(peak,equity);mdd=max(mdd,peak-equity)
  return {'n':len(xs),'nonvoid':len(nd),'hit_rate':float(np.mean([z[2] for x,z in nd])) if nd else None,'roi':float(np.mean([z[3] for z in data])) if data else None,'log_loss':float(np.mean([-math.log(max(1e-12,z[0] if x['price']['winner']=='home' else 1-z[0])) for x,z in nd])) if nd else None,'brier':float(np.mean([(z[0]-(x['price']['winner']=='home'))**2 for x,z in nd])) if nd else None,'mdd_units':mdd}
 names=['market','existing','additive','pair']
 def paired_ci(xs,ref):
  weeks=collections.defaultdict(list)
  for x in xs:weeks[datetime.date.fromisoformat(x['date']).isocalendar()[:2]].append(x)
  rng=np.random.default_rng(20261108); keys=list(weeks); deltas=[]
  for _ in range(2000):
   sample=[x for k in rng.integers(0,len(keys),len(keys)) for x in weeks[keys[k]]]
   aa=metrics(sample,ref);bb=metrics(sample,'pair')
   deltas.append([bb[k]-aa[k] for k in ['hit_rate','roi','log_loss','brier']])
  return {k:np.quantile(np.array(deltas)[:,i],[.025,.975]).tolist() for i,k in enumerate(['hit_rate','roi','log_loss','brier'])}
 priced=[x for x in pred if x['price']]; missing=[x for x in priced if x['existing_p'] is None]
 def describe(xs):
  return {'n':len(xs),'first_date':min(x['date'] for x in xs),'last_date':max(x['date'] for x in xs),
          'months':dict(sorted(collections.Counter(x['date'][:7] for x in xs).items())),
          'winner':dict(collections.Counter(x['price']['winner'] for x in xs)),
          'changed_pair_vs_market':sum(vals_for(x,'pair')[1]!=vals_for(x,'market')[1] for x in xs),
          'metrics':{n:metrics(xs,n) for n in ['market','additive','pair']}}
 result={'oracle_priced_only_n':len(priced),'oracle_priced_only_draws':sum(x['price']['winner']=='draw' for x in priced),'oracle_priced_only_metrics':{n:metrics(priced,n) for n in ['market','additive','pair']},'all_eval_run_mse':{name:float(np.mean([(p-y)**2 for x in pred for p,y in zip(x[name+'_runs'],[x['home_runs'],x['away_runs']])])) for name in ['additive','pair']},'train':len(train),'validation':len(valid),'eval':len(test),'validation_mse':vals,'selected_pair_penalty':pen,'strict_pregame_n':0,'strict_reason':'no historical lineup or price receipt time','oracle_price_existing_matched_n':len(common),'matched_draws':sum(x['price']['winner']=='draw' for x in common),'metrics':{n:metrics(common,n) for n in names},'monthly':{month:{n:metrics([x for x in common if x['date'][:7]==month],n) for n in names} for month in sorted({x['date'][:7] for x in common})},'changed_picks':sum(vals_for(x,'pair')[1]!=vals_for(x,'additive')[1] for x in common),'pair_minus_additive_weekly_bootstrap_95':paired_ci(common,'additive'),'pair_minus_market_weekly_bootstrap_95_common':paired_ci(common,'market'),'pair_minus_existing_weekly_bootstrap_95_common':paired_ci(common,'existing'),'pair_minus_market_weekly_bootstrap_95_priced':paired_ci(priced,'market'),'priced_with_existing':describe(common),'priced_missing_existing':describe(missing),'unpriced_eval':sum(x['price'] is None for x in pred),'eval_draws':sum(x['home_runs']==x['away_runs'] for x in pred),'seed':20261108}
 (P/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8');print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 import sys
 if '--extract' in sys.argv:extract()
 run()

