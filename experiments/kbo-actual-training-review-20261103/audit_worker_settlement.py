"""Compare final worker results against pinned independent settlement and source ledgers."""
import ast,collections,gzip,hashlib,json,subprocess,types
from decimal import Decimal
from pathlib import Path
import reference as ref

MODEL=Path(r'C:\Users\user\Documents\Codex\kbtmodel');REVIEW=Path(r'C:\Users\user\Documents\Codex\kbtreview');EFFECTS=Path(r'C:\Users\user\Documents\Codex\kbeffects')
FINAL='54dbcfcdd9ffc3ca0ad473cf9d586a38398cde4e';PRE='ff2bef35988eb5add154b4bebf8b265f42792db1';OUR='f4a3e3d300e02406601d844f86a6eb50d54a76ec';OLD='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';P='experiments/kbo-actual-training-20261103/';R='experiments/kbo-actual-training-review-20261103/';E='experiments/kbo-player-effects-20261103/inputs/'
def blob(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def near(a,b,path):
 if a is None or b is None:need(a is None and b is None,'null mismatch '+path)
 elif isinstance(a,dict) and isinstance(b,dict):
  need(set(a)==set(b),'key mismatch '+path)
  for k in a:near(a[k],b[k],path+'/'+k)
 elif isinstance(a,list) and isinstance(b,list):
  need(len(a)==len(b),'length mismatch '+path)
  for i,(x,y) in enumerate(zip(a,b)):near(x,y,path+'/'+str(i))
 elif isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):need(abs(a-b)<=1e-11*max(1,abs(a),abs(b)),'number mismatch '+path+': '+str((a,b)))
 else:need(a==b,'value mismatch '+path+': '+str((a,b)))
def decimal_equal(a,b,path):need(Decimal(str(a))==Decimal(str(b)),'decimal mismatch '+path+': '+str((a,b)))
def main():
 worker_raw=blob(MODEL,FINAL,P+'artifacts/results.json');need(sha(worker_raw)=='ea837755a04a1340982cdda5469b2d1f4128816aff228b2266740ad47fb29837','worker result hash');w=json.loads(worker_raw)
 ours=json.loads(blob(REVIEW,OUR,R+'audit-independent-settlement.json'));predraw=blob(MODEL,FINAL,P+'artifacts/predictions.json');need(predraw==blob(MODEL,PRE,P+'artifacts/predictions.json') and sha(predraw)==w['prediction_sha256']==ours['prediction_sha256'],'prediction changed or hash mismatch')
 need(blob(MODEL,FINAL,P+'artifacts/final-models.json')==blob(MODEL,PRE,P+'artifacts/final-models.json'),'models changed')
 indexfinal=blob(MODEL,FINAL,P+'inputs/snapshot-index.json');need(indexfinal==blob(MODEL,PRE,P+'inputs/snapshot-index.json'),'input index changed');index=json.loads(indexfinal)
 for name,h in index['files'].items():need(sha(blob(MODEL,FINAL,P+'inputs/'+name))==h and blob(MODEL,FINAL,P+'inputs/'+name)==blob(MODEL,PRE,P+'inputs/'+name),'input blob changed '+name)
 need(w['prediction_commit']==PRE and w['training_count']==2182 and w['target_count']==709 and w['historical_receipt_verified'] is False and w['actual_wallet_replay'] is False,'worker provenance')
 counts=collections.Counter();
 for scenario in ('actual','projected'):
  got=w['results'][scenario];own=ours['scenarios'][scenario];need(set(got['outcome'])==set(own['outcomes']),'outcome model set')
  for name,x in got['outcome'].items():
   y=own['outcomes'][name];t=x['threeway']
   for left,right in [('n','n'),('hits','hits'),('accuracy','accuracy'),('logloss','logloss'),('brier','brier')]:near(t[left],y[right],scenario+'/'+name+'/'+left)
   for left,right in [('n','n'),('draws','draws'),('mean_predicted_draw','mean_draw_probability'),('decisive','decisive'),('decisive_hits','decisive_hits'),('decisive_accuracy','conditional_accuracy'),('decisive_logloss','conditional_logloss'),('decisive_brier','conditional_brier')]:near(x[left],y[right],scenario+'/'+name+'/'+left)
   near(x['observed_draw_rate'],y['draws']/y['n'],'draw rate');counts['outcome_models']+=1
  near(got['outcome_paired'],own['outcome_paired'],scenario+'/outcome_paired');counts['outcome_paired_intervals']+=len(got['outcome_paired'])
  need(set(got['price_cohorts'])==set(own['price_cohorts']),'price tiers')
  for tier,z in got['price_cohorts'].items():
   q=own['price_cohorts'][tier];near(z['n'],q['fixtures'],'price denominator');near(z['excluded_current_reject'],q['current_reject_excluded'],'reject count');need(set(z['models'])==set(q['models']),'price model names')
   for name,x in z['models'].items():
    y=q['models'][name]
    for left,right in [('bets','bets'),('hits','hits'),('voids','draw_refunds'),('accuracy_decisive','decisive_accuracy'),('logloss_decisive','conditional_logloss'),('brier_decisive','conditional_brier'),('roi_including_refunds','roi')]:near(x[left],y[right],scenario+'/'+tier+'/'+name+'/'+left)
    need(x['decisive']==y['bets']-y['draw_refunds'],'decisive denominator');decimal_equal(x['net_units'],y['net_units'],'net');decimal_equal(x['max_daily_close_drawdown_units'],y['max_daily_close_drawdown_units'],'drawdown');need(set(x['monthly_units'])==set(y['monthly_units']),'month set')
    for month in x['monthly_units']:decimal_equal(x['monthly_units'][month],y['monthly_units'][month],'month '+month)
    counts['price_models']+=1
   need(set(z['paired'])==set(q['paired']),'paired comparisons')
   for k,x in z['paired'].items():
    y=q['paired'][k];near(x['weeks'],y['weeks'],'weeks');near(x['changed'],y['changed'],'changed');near(x['accuracy_delta_ci95'],y['accuracy_ci95'],'accuracy CI');near(x['roi_delta_ci95'],y['roi_ci95'],'ROI CI');counts['paired_price_intervals']+=1
 # Recheck every emitted per-match prediction, draw refund and decimal settlement.
 pred=json.loads(predraw)['predictions'];src=json.loads(blob(EFFECTS,OLD,E+'price-ledgers.json'))['ledgers'];labels={x['fixture_id']:x for x in [json.loads(v) for v in gzip.decompress(blob(EFFECTS,OLD,E+'test-labels.jsonl.gz')).splitlines()]};reject={x['fixture_id']:x for x in [json.loads(v) for v in gzip.decompress(blob(EFFECTS,OLD,E+'price-reject-audit.jsonl.gz')).splitlines()]}
 for scenario,tiers in w['ledgers'].items():
  maps={name:{x['fixture_id']:x for x in rows} for name,rows in pred[scenario].items()}
  for tier,ledger in tiers.items():
   base=tier.removesuffix('_current_reject_excluded');source={x['fixture_id']:x for x in src[base]};seen=set();target_ids=set(next(iter(maps.values())));expected_ids=set(source)&target_ids
   if tier.endswith('_current_reject_excluded'):
    expected_ids={fid for fid in expected_ids if source[fid]['price_source']['origin']=='official_restored' or reject[fid]['official_result']['buyReject']=='0'}
   for r in ledger:
    fid=r['fixture_id'];need(fid not in seen and fid in source and fid in labels,'ledger identity');seen.add(fid);origin=source[fid]['price_source']['origin'];rejected=False if origin=='official_restored' else reject[fid]['official_result']['buyReject']!='0';need(r['current_buy_reject']==rejected,'buy rejection');need(not tier.endswith('_current_reject_excluded') or not rejected,'excluded rejection still present')
    y=labels[fid];winner='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';need(r['winner']==winner and r['price_source']==source[fid]['price_source'],'outcome or odds');prices=[r['price_source']['prices'][k] for k in ('home','away')]
    for name,x in r['forecasts'].items():
     q=maps[name][fid]['probabilities_2way'];prob=[q['home'],q['away']];near(x['probabilities'],prob,'per-match probability');pick,net=ref.settle(prob,prices,winner);need(x['pick']==pick and x['void']==(winner=='draw') and x['hit']==(winner!='draw' and pick==winner),'per-match pick');decimal_equal(x['net'],net,'per-match net');counts['decimal_fixture_settlements']+=1
   need(seen==expected_ids and len(ledger)==ours['scenarios'][scenario]['price_cohorts'][tier]['fixtures'],'ledger fixture set')
 # Run prior and changed validator against same cross-side poison examples.
 def get_validator(commit):
  source=blob(MODEL,commit,P+'dataset.py').decode('utf8');node=next(z for z in ast.parse(source).body if isinstance(z,ast.FunctionDef) and z.name=='check_shared');module=ast.Module(body=[node],type_ignores=[]);namespace={'p':types.SimpleNamespace(FEATURES=('home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength'),ROSTER_FEATURES=('home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength'))};exec(compile(module,'pinned','exec'),namespace);return namespace['check_shared']
 old=get_validator(PRE);new=get_validator(FINAL)
 for role in ('hitter','starter'):
  def row():return {'raw_features':{s+'_'+k:{'value':None} for s in ('home','away') for k in ('lineup_strength','starter_strength')},'roster':{s:{'starter':None} for s in ('home','away')}}
  l=row();r=row()
  if role=='hitter':l['raw_features']['home_lineup_strength']['value']=[{'player_id':'X','history':[1]}];r['raw_features']['away_lineup_strength']['value']=[{'player_id':'X','history':[2]}]
  else:
   l['roster']['home']['starter']='X';r['roster']['away']['starter']='X';l['raw_features']['home_starter_strength']['value']={'projected_player_id':'X','appearances':[1]};r['raw_features']['away_starter_strength']['value']={'projected_player_id':'X','appearances':[2]}
  old(l,r)
  try:new(l,r)
  except ValueError:counts['cross_side_regressions_rejected']+=1
  else:raise ValueError('new validator accepts '+role+' cross-side poison')
 print(json.dumps({'worker_commit':FINAL,'worker_results_sha256':sha(worker_raw),'independent_commit':OUR,'prediction_commit':PRE,'input_blobs_unchanged':len(index['files']),'models_unchanged':True,'predictions_unchanged':True,'counts':counts,'all_compared_metrics_and_intervals_match':True,'official_2026_scores_matched':ours['official_score_comparison']['matched']},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
