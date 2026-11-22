"""Replay frozen 2026 outcome/price settlement with Decimal and paired week bootstrap."""
import base64,collections,gzip,hashlib,json,math
from datetime import date
from decimal import Decimal
import numpy as np
from audit_development_frozen import REPO,blob,sha,need
from audit_final_predictions import FINAL
import reference as ref
from pathlib import Path
E=Path(r'C:\Users\user\Documents\Codex\kbeffects');D=Path(r'C:\Users\user\Documents\Codex\kbtdata');EC='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';DC='c4e6bdb67784ceaf76e39707e1b8083f222c0a7d';EP='experiments/kbo-player-effects-20261103/inputs/';DP='experiments/kbo-actual-training-data-20261103/';P='experiments/kbo-actual-training-20261103/'
def gz(raw):return [json.loads(x) for x in gzip.decompress(raw).splitlines()]
def indexed(rows):
 d={x['fixture_id']:x for x in rows};need(len(d)==len(rows),'duplicate fixture');return d
def outcome(y):return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
def weekly_outcome(rows,first,second):
 groups=collections.defaultdict(lambda:np.zeros(4));changed=0
 for x in rows:
  g=groups[date.fromisoformat(x['date']).isocalendar()[:2]];y=x['winner'];a=x['models'][first];b=x['models'][second]
  def score(p):return [int(max(('home','draw','away'),key=p.get)==y),-math.log(max(p[y],1e-15)),sum((p[k]-int(k==y))**2 for k in ('home','draw','away'))]
  g+=np.r_[1,np.array(score(a))-np.array(score(b))];changed+=int(max(('home','draw','away'),key=a.get)!=max(('home','draw','away'),key=b.get))
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);z=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(1)
 return {'weeks':len(groups),'changed':changed,'accuracy_delta_ci95':np.quantile(z[:,1]/z[:,0],[.025,.975]).tolist(),'logloss_delta_ci95':np.quantile(z[:,2]/z[:,0],[.025,.975]).tolist(),'brier_delta_ci95':np.quantile(z[:,3]/z[:,0],[.025,.975]).tolist()}
def main():
 pb=blob(REPO,FINAL,P+'artifacts/predictions.json');need(sha(pb)=='b41b8dc71af26ccfb5b17ad568309dd4bf742df15e2ba8aaf5091eda693aabbd','prediction blob');pred=json.loads(pb)
 files={'test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','price-ledgers.json':'24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23','price-reject-audit.jsonl.gz':'629c26c049669b07c6f0fc7413cd9c55a86beba24b20ce75767f2f54e82a2d93'};data={k:blob(E,EC,EP+k) for k in files}
 for k,h in files.items():need(sha(data[k])==h,'price or outcome blob '+k)
 labels=indexed(gz(data['test-labels.jsonl.gz']));prices=json.loads(data['price-ledgers.json'])['ledgers'];reject=indexed(gz(data['price-reject-audit.jsonl.gz']));starters=blob(D,DC,DP+'actual-starters-2026.jsonl.gz');evidence=blob(D,DC,DP+'actual-starters-2026-raw.jsonl.gz');need(sha(starters)=='ca1d6dbc1f301ee4681237cbf1cd864df5079a3ab9364c2f7b50129a9574a69d' and sha(evidence)=='1203fd23f085c56937273a5a42c0780ed703369b4b715fededc0a84a82e6e786','official bundle SHA')
 official=indexed(gz(starters));raws={}
 for z in gz(evidence):
  b=base64.b64decode(z['raw_base64'],validate=True);need(sha(b)==z['sha256'] and len(b)==z['bytes'],'official raw SHA');raws[z['sha256']]=b
 scenarios={s:{k:indexed(v) for k,v in models.items()} for s,models in pred['predictions'].items()};names=('M0_actual','M0_projected','M2_actual','M2_projected','oldfull_M0','oldfull_M2');comparisons=(('M0_actual','M0_projected'),('M2_actual','M2_projected'),('M2_actual','M0_actual'),('M2_projected','M0_projected'),('oldfull_M0','M0_projected'),('oldfull_M2','M2_projected'))
 ids=set(official);need(len(ids)==709 and all(set(models)==set(names) and all(set(x)==ids for x in models.values()) for models in scenarios.values()),'prediction population')
 for fid in ids:
  r=official[fid];y=labels[fid];need(y['status']=='final' and y['status_basis']=='archived_score_not_independently_official' and y['settlement_rule'] in (None,'draw_void_refund'),'archived outcome provenance');m=r['source_evidence']['schedule'];schedule=json.loads(raws[m['sha256']]);games=[x for x in schedule['game'] if x['G_ID']==r['official_game_id']];need(len(games)==1,'official result game mismatch');g=games[0];need(str(g['GAME_STATE_SC'])=='3' and str(g['GAME_RESULT_CK'])=='1' and str(g['B_SCORE_CN']).isdigit() and str(g['T_SCORE_CN']).isdigit(),'official final score missing');need((int(g['B_SCORE_CN']),int(g['T_SCORE_CN']))==(y['home_score'],y['away_score']),'official score mismatch '+fid)
 result={'prediction_commit':FINAL,'prediction_sha256':sha(pb),'score_source_commit':EC,'official_score_comparison':{'matched':len(ids),'mismatch':0,'missing':0,'basis':'same-game official schedule currently retrieved; archive label status unchanged'},'weekly_bootstrap':{'resamples':5000,'seed':20261103,'unit':'ISO calendar week','interval':'percentile 2.5-97.5'},'historical_receipt_proven':False,'actual_execution_proven':False,'scenarios':{}}
 for scenario,models in scenarios.items():
  outcome_rows=[]
  for fid in sorted(ids,key=lambda x:(models[names[0]][x]['event_date'],x)):
   y=labels[fid];winner=outcome(y);outcome_rows.append({'fixture_id':fid,'date':models[names[0]][fid]['event_date'],'winner':winner,'models':{name:models[name][fid]['probabilities_3way'] for name in names}})
  out={'outcomes':{name:ref.outcome_metrics([{'probabilities':z['models'][name],'winner':z['winner']} for z in outcome_rows]) for name in names},'outcome_paired':{a+'_vs_'+b:weekly_outcome(outcome_rows,a,b) for a,b in comparisons},'price_cohorts':{}}
  for cohort,old in prices.items():
   ledger=[];seen=set()
   for x in old:
    fid=x['fixture_id']
    if fid not in ids:continue
    need(fid not in seen,'duplicate price fixture');seen.add(fid);winner=outcome(labels[fid]);need(winner==x['winner'] and x['date']==models[names[0]][fid]['event_date'],'price outcome/date mismatch '+fid)
    odds=[x['price_source']['prices']['home'],x['price_source']['prices']['away']];origin=x['price_source']['origin']
    if origin=='official_restored':rejected=False
    else:need(origin=='original_archived' and fid in reject,'reject provenance');rejected=reject[fid]['official_result']['buyReject']!='0'
    ledger.append({'date':x['date'],'fixture_id':fid,'prices':odds,'winner':winner,'rejected':rejected,'models':{n:[models[n][fid]['probabilities_2way'][k] for k in ('home','away')] for n in names}})
   for suffix,current in (('',ledger),('_current_reject_excluded',[r for r in ledger if not r['rejected']])):
    metrics={}
    for name in names:
     n=len(current);hits=draws=0;net=Decimal(0);daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);ll=brier=0.
     for r in current:
      prob=r['models'][name];pick,value=ref.settle(prob,r['prices'],r['winner']);net+=value;daily[r['date']]+=value;monthly[r['date'][:7]]+=value
      if r['winner']=='draw':draws+=1
      else:
       hits+=int(pick==r['winner']);j=0 if r['winner']=='home' else 1;ll-=math.log(max(prob[j],1e-15));brier+=sum((v-int(i==j))**2 for i,v in enumerate(prob))
     total=peak=dd=Decimal(0)
     for dt in sorted(daily):total+=daily[dt];peak=max(peak,total);dd=max(dd,peak-total)
     metrics[name]={'bets':n,'hits':hits,'draw_refunds':draws,'decisive_accuracy':hits/(n-draws) if n>draws else None,'conditional_logloss':ll/(n-draws) if n>draws else None,'conditional_brier':brier/(n-draws) if n>draws else None,'net_units':str(net),'roi':float(net/n) if n else None,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'max_daily_close_drawdown_units':str(dd)}
    pairs={}
    for a,b in comparisons:
     rr=[{'date':r['date'],'a':r['models'][a],'b':r['models'][b],'prices':r['prices'],'winner':r['winner']} for r in current];pairs[a+'_vs_'+b]=ref.paired_weekly(rr)
    out['price_cohorts'][cohort+suffix]={'fixtures':len(current),'current_reject_excluded':len(ledger)-len(current),'models':metrics,'paired':pairs}
  result['scenarios'][scenario]=out
 print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
