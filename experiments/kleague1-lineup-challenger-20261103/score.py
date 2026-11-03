"""Score only previously committed, label-free predictions; no tuning."""
import collections,datetime,decimal,gzip,hashlib,json,math,subprocess
from pathlib import Path
import numpy as np
import importlib.util
def _local_module(name):
    path=Path(__file__).resolve().with_name(name+'.py')
    spec=importlib.util.spec_from_file_location('k1_lineup_ablation_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

data=_local_module('data');model=_local_module('model')
load_inputs,partition,ROOT,INPUT=data.load_inputs,data.partition,data.ROOT,data.INPUT
history_features=model.history_features

FREEZE='77bb7c3ebe3dee87bd75fca5046b05577a9996f2'
NBOOT=2000;SEED=20261103

def winner(score):return 0 if score[0]>score[1] else 1 if score[0]==score[1] else 2

def pick(p):
 if len(p)!=3 or any(not math.isfinite(x) or x<0 for x in p) or abs(sum(p)-1)>1e-10:raise ValueError('invalid probability vector')
 return max(range(3),key=lambda i:(p[i],-i))

def payoff(selection,y,odds):
 return (decimal.Decimal(str(odds[selection]))-1) if selection==y else decimal.Decimal(-1)

def metrics(rows,name,priced):
 p=[r[name+'_p'] for r in rows];ys=[r['y'] for r in rows];choices=[pick(q) for q in p]
 out={'n':len(rows),'hits':sum(c==y for c,y in zip(choices,ys)),'accuracy':sum(c==y for c,y in zip(choices,ys))/len(rows),
  'logloss':sum(-math.log(max(q[y],1e-15)) for q,y in zip(p,ys))/len(rows),
  'brier':sum(sum((q[j]-(j==y))**2 for j in range(3)) for q,y in zip(p,ys))/len(rows)}
 if priced:
  pay=[payoff(c,y,r['odds']) for c,y,r in zip(choices,ys,rows)]
  daily=collections.defaultdict(decimal.Decimal);monthly=collections.defaultdict(decimal.Decimal)
  for r,v in zip(rows,pay):daily[r['day']]+=v;monthly[r['day'][:7]]+=v
  cumulative=decimal.Decimal(0);peak=decimal.Decimal(0);dd=decimal.Decimal(0)
  for day in sorted(daily):cumulative+=daily[day];peak=max(peak,cumulative);dd=max(dd,peak-cumulative)
  out.update(net=float(sum(pay,decimal.Decimal(0))),roi=float(sum(pay,decimal.Decimal(0))/len(rows)),monthly_net={k:float(v) for k,v in sorted(monthly.items())},daily_close_max_drawdown=float(dd))
 return out

def paired_ci(rows,left,right):
 groups=collections.defaultdict(list)
 for row in rows:
  d=datetime.date.fromisoformat(row['day']);groups[(d-datetime.timedelta(days=d.weekday())).isoformat()].append(row)
 weeks=sorted(groups);rng=np.random.default_rng(SEED);diffs=[]
 for _ in range(NBOOT):
  chosen=rng.integers(0,len(weeks),size=len(weeks));sample=[r for j in chosen for r in groups[weeks[j]]]
  def vals(name):
   choices=[pick(r[name+'_p']) for r in sample]
   acc=sum(c==r['y'] for c,r in zip(choices,sample))/len(sample)
   roi=sum(float(payoff(c,r['y'],r['odds'])) for c,r in zip(choices,sample))/len(sample)
   ll=sum(-math.log(max(r[name+'_p'][r['y']],1e-15)) for r in sample)/len(sample)
   return np.array([acc,roi,ll])
  diffs.append(vals(left)-vals(right))
 a=np.asarray(diffs);return {'weeks':len(weeks),'resamples':NBOOT,'seed':SEED,'lineup_minus_reference':{k:np.quantile(a[:,i],[.025,.975]).tolist() for i,k in enumerate(('accuracy','roi','logloss'))}}

def score_rows(predictions,games,source_prices):
 by_id={g['id']:g for g in games};seen=set();all_rows=[];priced=[];excluded=collections.Counter()
 for f in predictions:
  key=f['fixture_id'];g=by_id[key]
  if key in seen or f['day']!=g['date'].isoformat() or f['teams']!=g['teams']:raise ValueError('frozen identity conflict')
  seen.add(key);y=winner(g['score']);row={'id':key,'day':f['day'],'y':y,'score':g['score'],'team_p':f['team_p'],'lineup_p':f['lineup_p']}
  all_rows.append(row)
  if f['price'] is None:excluded['missing_price']+=1;continue
  raw=source_prices.get(key)
  if not raw:raise ValueError('frozen price source missing')
  if str(raw.get('settlement_code')) not in {'0','1','2'}:excluded['refund_or_nonstandard_settlement']+=1;continue
  if raw.get('official_score')!=g['score'] or int(raw['settlement_code'])!=y:raise ValueError('score or official settlement mismatch')
  q=f['price'];ids=[raw.get('gmId'),raw.get('gmTs'),raw.get('matchSeq')]
  if ids!=q['product_id'] or [float(raw['prices'][k]) for k in ('home','draw','away')]!=q['odds']:raise ValueError('frozen price mismatch')
  market=f['market_p'];expected=np.reciprocal(q['odds']);expected/=expected.sum()
  if not np.allclose(market,expected,atol=1e-12,rtol=0):raise ValueError('market probability mismatch')
  priced.append({**row,'odds':q['odds'],'product_id':q['product_id'],'market_p':market})
 return all_rows,priced,dict(excluded)

def main():
 frozen=ROOT/'frozen';raw=(frozen/'predictions.jsonl').read_bytes();meta=json.loads((frozen/'manifest.json').read_bytes())
 if hashlib.sha256(raw).hexdigest()!=meta['predictions_sha256']:raise ValueError('frozen SHA mismatch')
 for name in ('predictions.jsonl','manifest.json'):
  blob=subprocess.check_output(['git','show',FREEZE+':'+(frozen/name).relative_to(Path.cwd()).as_posix()])
  if blob!=(frozen/name).read_bytes():raise ValueError('not the committed frozen artifact')
 predictions=[json.loads(x) for x in raw.splitlines()];games,_=load_inputs(scoring=True);train,val,embargo,test=partition(games)
 features=history_features(games)
 history_coverage={name:{'fixtures':len(part),'sides_previous_missing':sum(int(x[2]) for g in part for x in features[g['id']]),'sides_fewer_than_three':sum(int(x[3]) for g in part for x in features[g['id']])} for name,part in (('train',train),('validation',val),('embargo',embargo),('test',test))}
 if {x['fixture_id'] for x in predictions}!={g['id'] for g in test}:raise ValueError('test cohort mismatch')
 with gzip.open(INPUT/'prices.jsonl.gz','rt',encoding='utf8') as f:src={r['fixture_id']:r for r in (json.loads(x) for x in f) if r.get('league_id')==1 and r.get('season') in (2025,2026)}
 allrows,priced,excluded=score_rows(predictions,test,src)
 out={'classification':'retrospective_lineup_known_reused_2026_not_blind','frozen_commit':FREEZE,'test_total':len(allrows),'price_common_n':len(priced),'price_excluded':excluded,
   'all_test_1x2':{k:metrics(allrows,k,False) for k in ('team','lineup')},
   'priced_common':{k:metrics(priced,k,True) for k in ('market','team','lineup')},
   'history_coverage_by_split':history_coverage,
   'changed_picks_all_test':{'lineup_vs_team':sum(pick(r['lineup_p'])!=pick(r['team_p']) for r in allrows)},
   'changed_picks':{'lineup_vs_team':sum(pick(r['lineup_p'])!=pick(r['team_p']) for r in priced),'lineup_vs_market':sum(pick(r['lineup_p'])!=pick(r['market_p']) for r in priced)},
   'paired_week_bootstrap':{k:paired_ci(priced,'lineup',k) for k in ('team','market')},
   'test_lineup_history':{'fixtures':len(predictions),'sides_previous_missing':sum(int(x[2]) for r in predictions for x in r['lineup_features']),'sides_fewer_than_three':sum(int(x[3]) for r in predictions for x in r['lineup_features']),'unknown_team_sides':sum(sum(r['lineup_unknown']) for r in predictions)},
   'limits':['Retrospective official actual XI and official stored prices; historical pregame receipt and execution unverified.','Reused 2026 and 114-test sample: not blind and not proof of edge.','No current operating 9:1/tipster frozen replay; flat-unit simulation is not monthly loss-limit wallet.']}
 dest=ROOT/'evaluation';dest.mkdir(exist_ok=True);ledger=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in priced).encode('utf8');(dest/'ledger.jsonl').write_bytes(ledger)
 out['ledger_sha256']=hashlib.sha256(ledger).hexdigest();(dest/'results.json').write_bytes((json.dumps(out,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
 print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
