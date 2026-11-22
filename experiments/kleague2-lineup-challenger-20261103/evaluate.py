"""Score frozen predictions exactly once; source prices are retrospective."""
import collections,datetime,gzip,hashlib,json,math,subprocess
from pathlib import Path
import numpy as np
import model
ROOT=Path(__file__).resolve().parent
FREEZE='e8288f5d92245bcdcb2f1a221966e228309eb465'
PRED_SHA='96006c7e54b335d31bc83a4c7bebc2ed07b31427ff196a567a9d41a8257a8872'
def dump(path,obj):path.write_bytes((json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8'))
def valid_price(row,g):
 if row.get('fixture_id')!=g['id'] or row.get('league_id')!=2 or row.get('season')!=g['date'].year:return False
 if row.get('gmId')!='G101' or row.get('betman_league_code')!='SC003':return False
 if [row.get('home_team_id'),row.get('away_team_id')]!=g['teams'] or row.get('kickoff_kst')!=g['kickoff']:return False
 if row.get('official_score')!=g['score']:return False
 if str(row.get('settlement_code'))!=str(0 if g['score'][0]>g['score'][1] else 1 if g['score'][0]==g['score'][1] else 2):return False
 if str(row.get('gameReject'))!='0' or str(row.get('buyReject'))!='0':return False
 mt=row.get('market_type') or {};a=mt.get('betId');b=mt.get('betTypId');name=mt.get('betNm')
 old=a is None and b is None and name is None
 new=str(a)=='1' and str(b)=='1' and name=='\ucd95\uad6c \uc2b9\ubb34\ud328'
 if not (old or new) or mt.get('handi')!=0:return False
 o=row.get('prices') or {};odds=[o.get('home'),o.get('draw'),o.get('away')]
 return all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>1 for x in odds)
def outcomes(g):return 0 if g['score'][0]>g['score'][1] else 1 if g['score'][0]==g['score'][1] else 2
def stats(rows,mode):
 y=np.array([r['outcome'] for r in rows]);p=np.array([r['p'][mode] for r in rows]);picks=np.argmax(p,axis=1);n=len(rows)
 if not n:return None
 one=np.eye(3)[y];return {'n':n,'accuracy':float(np.mean(picks==y)),'hits':int(np.sum(picks==y)),'logloss':float(np.mean(-np.log(p[np.arange(n),y]))),'brier':float(np.mean(np.sum((p-one)**2,axis=1)))}
def profit(r,mode):
 pick=int(np.argmax(r['p'][mode]));return float(r['odds'][pick]-1) if pick==r['outcome'] else -1.0
def priced_stats(rows,mode):
 if not rows:return None
 pnl=np.array([profit(r,mode) for r in rows]);monthly=collections.defaultdict(float);daily=collections.defaultdict(float)
 for r,amount in zip(rows,pnl):monthly[r['date'][:7]]+=float(amount);daily[r['date']]+=float(amount)
 equity=0.;peak=0.;dd=0.
 for date in sorted(daily):equity+=daily[date];peak=max(peak,equity);dd=max(dd,peak-equity)
 return {'n':len(rows),'roi':float(pnl.mean()),'profit_units':float(pnl.sum()),'monthly_profit_units':dict(sorted(monthly.items())),'daily_closing_max_drawdown_units':dd}
def bootstrap(rows,metric,reference='team_only'):
 groups=collections.defaultdict(list)
 for r in rows:
  day=datetime.date.fromisoformat(r['date']);year,week,_=day.isocalendar();groups[(year,week)].append(r)
 keys=sorted(groups);rng=np.random.default_rng(20261103);diff=[]
 for _ in range(2000):
  sample=[r for ix in rng.integers(0,len(keys),size=len(keys)) for r in groups[keys[int(ix)]]]
  if metric=='roi':d=priced_stats(sample,'lineup')['roi']-priced_stats(sample,reference)['roi']
  else:d=stats(sample,'lineup')[metric]-stats(sample,reference)[metric]
  diff.append(d)
 return {'weeks':len(keys),'resamples':2000,'seed':20261103,'ci95':[float(x) for x in np.quantile(diff,[.025,.975])]}
def main():
 p=ROOT/'frozen/test-predictions.jsonl';body=p.read_bytes()
 if model.sha(body)!=PRED_SHA:raise ValueError('frozen predictions hash mismatch')
 blob=subprocess.check_output(['git','show',FREEZE+':'+p.relative_to(ROOT.parent.parent).as_posix()],cwd=ROOT.parent.parent)
 if blob!=body:raise ValueError('frozen Git prediction differs')
 pred=[json.loads(l) for l in body.splitlines()];games,excluded=model.load_games();_,_,test=model.partition(games);by={g['id']:g for g in test}
 if len(pred)!=len(by) or len({r['fixture_id'] for r in pred})!=len(pred) or set(by)!={r['fixture_id'] for r in pred}:raise ValueError('frozen cohort mismatch')
 with gzip.open(ROOT/'inputs/joined-prices.jsonl.gz','rt',encoding='utf8') as f:prices=[json.loads(l) for l in f]
 price_by=collections.defaultdict(list)
 for quote in prices:price_by[quote.get('fixture_id')].append(quote)
 rows=[];reasons=collections.Counter()
 for rec in pred:
  g=by[rec['fixture_id']]
  if rec['kickoff_kst']!=g['kickoff'] or rec['team_ids']!=g['teams']:raise ValueError('frozen identity mismatch')
  fb=json.dumps(rec['features'],separators=(',',':')).encode('utf8')
  if hashlib.sha256(fb).hexdigest()!=rec['features_sha256']:raise ValueError('feature hash mismatch')
  probs={k:v['p'] for k,v in rec['models'].items()}
  for k,pv in probs.items():
   if len(pv)!=3 or any(x<=0 or not math.isfinite(x) for x in pv) or abs(sum(pv)-1)>1e-10 or int(np.argmax(pv))!=rec['models'][k]['pick']:raise ValueError('invalid frozen prediction')
  row={'id':g['id'],'date':g['date'].isoformat(),'outcome':outcomes(g),'p':probs,'odds':None}
  quotes=price_by.get(g['id'],[])
  if len(quotes)!=1:reasons['missing_quote' if not quotes else 'ambiguous_quote']+=1
  elif not valid_price(quotes[0],g):reasons['invalid_quote_identity_market_result_or_void']+=1
  else:
   q=quotes[0];row['odds']=[q['prices'][k] for k in ('home','draw','away')];inv=np.reciprocal(row['odds']);row['p']['market']=(inv/inv.sum()).tolist()
  rows.append(row)
 priced=[r for r in rows if r['odds'] is not None]
 summary={'status':'retrospective_diagnostic_not_certified_pregame','freeze_commit':FREEZE,'prediction_sha256':PRED_SHA,'cohort_n':len(rows),'test_all':{k:stats(rows,k) for k in ('team_only','lineup')},'test_paired_ci95':{k:bootstrap(rows,k) for k in ('accuracy','logloss','brier')},'test_changed_picks':int(sum(np.argmax(r['p']['team_only'])!=np.argmax(r['p']['lineup']) for r in rows)),'price_matched_n':len(priced),'price_exclusions':dict(reasons),'price_matched_accuracy_loss':{k:stats(priced,k) for k in ('team_only','lineup','market')},'price_matched_returns':{k:priced_stats(priced,k) for k in ('team_only','lineup','market')},'price_matched_candidate_minus_team_roi_ci95':bootstrap(priced,'roi') if priced else None,'price_changed_picks':int(sum(np.argmax(r['p']['team_only'])!=np.argmax(r['p']['lineup']) for r in priced)),'xi_feature_missing_team_sides':{'no_previous':sum(side[2] for rec in pred for side in rec['features']),'less_than_three':sum(side[3] for rec in pred for side in rec['features'])},'unknown_team_sides':{k:sum(sum(rec['models'][k]['unknown_teams']) for rec in pred) for k in ('team_only','lineup')},'excluded':excluded,'limitations':['Postmatch sourced official actual XI; no independent T-30 reception authentication.','Price snapshots are retrospective with no confirmed executable time.','No frozen operating model comparison; no deployment or recommendation.','No Cup, K1 or national-team history for player features.','Previously inspected 2026 sample; not blind.']}
 train,val,_=model.partition(games);all_features=model.history_features(games)
 summary['history_coverage_by_fold']={}
 for name,fold in [('train',train),('validation',val),('test',test)]:
  fs=[side for g in fold for side in all_features[g['id']]]
  summary['history_coverage_by_fold'][name]={'team_sides':len(fs),'no_previous':int(sum(side[2] for side in fs)),'fewer_than_three':int(sum(side[3] for side in fs))}
 if priced:
  summary['price_matched_lineup_minus_market_ci95']={k:bootstrap(priced,k,'market') for k in ('accuracy','logloss','brier','roi')}
 out=ROOT/'results';out.mkdir(exist_ok=True);dump(out/'metrics.json',summary)
 (out/'scored.jsonl').write_bytes(b''.join((json.dumps(r,sort_keys=True,separators=(',',':'))+'\n').encode('utf8') for r in rows))
 print(json.dumps(summary,ensure_ascii=True,indent=2))
if __name__=='__main__':main()
