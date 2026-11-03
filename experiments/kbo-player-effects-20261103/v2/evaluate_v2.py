"""Score frozen v2 M2 against unchanged v1 predictions and price ledgers."""
import collections,copy,hashlib,json,math,subprocess,sys
from datetime import date
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P));import research_v2 as m
r=m.v1;sys.path.insert(0,str(P.parent));import evaluate as e;import draw_diagnostic as diagnostic
COMMIT='18c2e84d6962f9016b20996e543e5b4bb6c70975';SHA='6d3dd2cc15d4a4681335e1984c76e8526bf94aae9f46fe372d5b4c10b1abced5';OLD_RESULT_SHA='211b89d365136a166fd13026df5f6358e4b7ecda5db50b4459a2f42e75f19cd8'
def frozen_v2():
 raw=(P/'predictions.json').read_bytes();blob=subprocess.check_output(['git','show',COMMIT+':experiments/kbo-player-effects-20261103/v2/predictions.json'])
 if hashlib.sha256(raw).hexdigest()!=SHA or raw!=blob:raise ValueError('v2 prediction freeze mismatch')
 o=json.loads(raw)
 if o['2026_labels_read'] is not False or o['historical_receipt_proven'] is not False:raise ValueError('invalid prediction state')
 return o

def outcome_pair(new,old,labels):
 old=r.index(old);groups=collections.defaultdict(lambda:np.zeros(4));changed2=changed3=0;delta=[]
 for x in new:
  prev=old[x['fixture_id']];truth=e.winner(labels[x['fixture_id']]);a=x['probabilities_3way'];b=prev['probabilities_3way'];pa=x['probabilities_2way'];pb=prev['probabilities_2way'];changed2+=max(('home','away'),key=pa.get)!=max(('home','away'),key=pb.get);changed3+=max(('home','draw','away'),key=a.get)!=max(('home','draw','away'),key=b.get);delta.extend(abs(a[k]-b[k]) for k in ('home','draw','away'))
  def scores(p):return np.array([int(max(('home','draw','away'),key=p.get)==truth),-math.log(max(p[truth],1e-15)),sum((p[k]-(k==truth))**2 for k in ('home','draw','away'))])
  groups[date.fromisoformat(x['event_date']).isocalendar()[:2]]+=np.r_[1,scores(a)-scores(b)]
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
 return {'weeks':len(arr),'changed_3way_picks':changed3,'changed_2way_picks':changed2,'mean_absolute_probability_change_3way_per_class':float(np.mean(delta)),**{k:np.quantile(boot[:,i]/boot[:,0],[.025,.975]).tolist() for i,k in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1)}}

def main():
 m.verify_contract();v2=frozen_v2();oldpred,labels,prices,audit,old=e.read_pinned();raw=(r.A/'results.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=OLD_RESULT_SHA or raw!=subprocess.check_output(['git','show','2156ec6b2c7c54a104c051c5cddc5a9d0b9d46f7:experiments/kbo-player-effects-20261103/artifacts/results.json']):raise ValueError('v1 results changed')
 oldresults=json.loads(raw);combined=copy.deepcopy(oldpred);results={};ledgers={}
 for s in r.SETTINGS:
  new=v2['scenarios'][s];old=oldpred['scenarios'][s]['M2'];combined['scenarios'][s]['M2']=new
  result,ledger=e.scenario_score(s,combined,labels,prices,audit,old=e.r.index(json.loads((r.I/'old-fresh-predictions.json').read_bytes())['predictions']))
  for model in ('M0','M1','old_fixed'):
   if result['threeway']['models'][model]!=oldresults['scenarios'][s]['threeway']['models'][model]:raise ValueError('unchanged v1 score differs')
  for tier,ls in ledger.items():
   old_ls={x['fixture_id']:x for x in oldresults['price_ledgers'][s][tier]}
   if set(old_ls)!={x['fixture_id'] for x in ls}:raise ValueError('price coverage changed')
   for x in ls:
    prior=old_ls[x['fixture_id']]
    if any(x['forecasts'][k]!=prior['forecasts'][k] for k in ('M0','M1','old_fixed','market','team')):raise ValueError('unchanged settlement differs')
    x['forecasts']['M2_v1']=prior['forecasts']['M2']
   result['prices'][tier]['M2_v1']=e.metric(ls,'M2_v1');result['prices'][tier]['M2_v2_vs_v1']=e.paired_price(ls,'M2','M2_v1')
  result['M2_v2_vs_v1']=outcome_pair(new,old,labels);result['draw_diagnostic']={'M2_v1':diagnostic.calc(old,labels),'M2_v2':diagnostic.calc(new,labels)};results[s]=result;ledgers[s]=ledger
 output={'v2_prediction_commit':COMMIT,'v2_prediction_sha256':SHA,'v1_result_sha256':OLD_RESULT_SHA,'selected_family':v2['selected_family'],'not_blind_prior_2026_seen':True,'unchanged_M0_M1_verified':True,'scenarios':results,'price_ledgers':ledgers};r.dump(P/'results.json',output);print(json.dumps({k:{'M2_v1':oldresults['scenarios'][k]['threeway']['models']['M2'],'M2_v2':v['threeway']['models']['M2'],'extended_v2':v['prices']['extended_day_team_source_confirmed']['models']['M2']['roi_including_refunds'],'extended_v1':v['prices']['extended_day_team_source_confirmed']['M2_v1']['roi_including_refunds']} for k,v in results.items()}))
if __name__=='__main__':main()
