"""Independent scalar and Decimal verification of saved scored ledger; no fitting."""
import collections,decimal,gzip,hashlib,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
D=decimal.Decimal

def main():
 frozen=json.loads((ROOT/'frozen/manifest.json').read_bytes());predbytes=(ROOT/'frozen/predictions.jsonl').read_bytes()
 if hashlib.sha256(predbytes).hexdigest()!=frozen['predictions_sha256']:raise ValueError('frozen sha')
 predicted={r['fixture_id']:r for r in map(json.loads,predbytes.splitlines())}
 source={}
 for name in ('lineups2025.jsonl','lineups2026.jsonl'):
  for r in map(json.loads,(ROOT/'inputs'/name).read_bytes().splitlines()):
   if r['fixture_id'] in predicted:
    if r['fixture_id'] in source:raise ValueError('duplicate official ID')
    source[r['fixture_id']]=r
 ledgerbytes=(ROOT/'evaluation/ledger.jsonl').read_bytes();out=json.loads((ROOT/'evaluation/results.json').read_bytes())
 if hashlib.sha256(ledgerbytes).hexdigest()!=out['ledger_sha256']:raise ValueError('ledger sha')
 rows=list(map(json.loads,ledgerbytes.splitlines()))
 with gzip.open(ROOT/'inputs/prices.jsonl.gz','rt',encoding='utf8') as f:
  prices={r['fixture_id']:r for r in map(json.loads,f) if r.get('league_id')==1}
 if len(source)!=114 or len(rows)!=113 or len({r['id'] for r in rows})!=113:raise ValueError('unexpected cohort')
 for name in ('team','lineup'):
  hits=0;loss=0.;brier=0.
  for key,f in predicted.items():
   x=source[key];scores=[x['scores']['home'],x['scores']['away']]
   if f['day']!=x['day'] or f['teams']!=[t['team_id'] for t in x['lineups']]:raise ValueError('full fixture identity mismatch')
   actual=0 if scores[0]>scores[1] else 1 if scores[0]==scores[1] else 2;q=f[name+'_p']
   chosen=max(range(3),key=lambda i:(q[i],-i));hits+=chosen==actual
   loss-=math.log(max(q[actual],1e-15));brier+=sum((q[i]-(i==actual))**2 for i in range(3))
  actuals={'n':len(predicted),'hits':hits,'accuracy':hits/len(predicted),'logloss':loss/len(predicted),'brier':brier/len(predicted)}
  for k,v in actuals.items():
   if abs(out['all_test_1x2'][name][k]-v)>1e-10:raise ValueError('full '+name+' '+k)
 reports={}
 for name in ('market','team','lineup'):
  hits=0;loss=0.;brier=0.;monthly=collections.defaultdict(D);daily=collections.defaultdict(D)
  for r in rows:
   q=r[name+'_p'];x=source[r['id']];scores=[x['scores']['home'],x['scores']['away']];actual=0 if scores[0]>scores[1] else 1 if scores[0]==scores[1] else 2
   if r['score']!=scores or r['y']!=actual:raise ValueError('wrong score')
   raw=prices[r['id']];official=[raw['prices'][k] for k in ('home','draw','away')]
   if r['odds']!=official or r['product_id']!=[raw['gmId'],raw['gmTs'],raw['matchSeq']] or raw['official_score']!=scores or str(raw['settlement_code'])!=str(actual):raise ValueError('price or official settlement mismatch')
   original=predicted[r['id']]
   if q!=original[name+'_p']:raise ValueError('probability differs from frozen prediction')
   chosen=max(range(3),key=lambda i:(q[i],-i));hits+=chosen==actual;loss-=math.log(max(q[actual],1e-15));brier+=sum((q[i]-(i==actual))**2 for i in range(3))
   net=(D(str(official[chosen]))-D(1)) if chosen==actual else D(-1);daily[r['day']]+=net;monthly[r['day'][:7]]+=net
  total=sum(daily.values(),D(0));peak=D(0);cur=D(0);dd=D(0)
  for day in sorted(daily):cur+=daily[day];peak=max(peak,cur);dd=max(dd,peak-cur)
  n=len(rows);result=out['priced_common'][name]
  expected={'n':n,'hits':hits,'accuracy':hits/n,'logloss':loss/n,'brier':brier/n,'net':float(total),'roi':float(total/D(n)),'daily_close_max_drawdown':float(dd)}
  for k,v in expected.items():
   if abs(result[k]-v)>1e-10:raise ValueError(name+' '+k)
  if result['monthly_net']!={k:float(v) for k,v in sorted(monthly.items())}:raise ValueError('monthly mismatch')
  reports[name]={'n':n,'hits':hits,'net_exact':str(total),'drawdown_exact':str(dd)}
 print(json.dumps({'fixture_labels_checked':len(source),'settlement_checks':len(rows)*3,'mismatches':0,'results':reports},ensure_ascii=False))
if __name__=='__main__':main()
