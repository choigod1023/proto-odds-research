"""Evaluate only committed 2026 predictions on fixed labels and prices."""
import collections,decimal,gzip,hashlib,json,math,subprocess
from datetime import date
from pathlib import Path
import numpy as np
import research as r
D=decimal.Decimal;P=r.P;I=r.I;A=r.A
FREEZE='433d915aa52727de4af128cd9e614ca80aa8aa72';SHA='ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9'
NAMES=('M0','M1','M2','old_fixed','team','market')
def read_pinned():
 raw=(A/'predictions.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=SHA or raw!=subprocess.check_output(['git','show',FREEZE+':experiments/kbo-player-effects-20261103/artifacts/predictions.json']):raise ValueError('prediction freeze mismatch')
 pred=json.loads(raw)
 if pred['2026_labels_read'] is not False or pred['historical_receipt_proven'] is not False:raise ValueError('invalid scenario')
 manifest=json.loads((I/'evaluation-manifest.json').read_bytes())
 expected={'test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','price-ledgers.json':'24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23','old-fresh-predictions.json':'9bcb16da33843f3e60b869c62d4b2cb67f65e346b87c1be215e49a2b9b5c1f0e','price-reject-audit.jsonl.gz':'629c26c049669b07c6f0fc7413cd9c55a86beba24b20ce75767f2f54e82a2d93','source-labels.jsonl.gz':'1abe7313d5f71cfc3e7a7600665fd2d5f97e4230f1ce6afe7a597f561cfd6e2b'}
 if manifest['files']!=expected:raise ValueError('unexpected evaluation source pins')
 for name,sha in manifest['files'].items():
  if hashlib.sha256((I/name).read_bytes()).hexdigest()!=sha:raise ValueError('evaluation input hash mismatch '+name)
 if hashlib.sha256(gzip.decompress((I/'source-labels.jsonl.gz').read_bytes())).hexdigest()!=manifest['raw_source_labels_sha256']:raise ValueError('source label hash mismatch')
 labels=r.index(r.read_gz('test-labels.jsonl.gz'));source=r.index(r.read_gz('source-labels.jsonl.gz'));audit=r.index(r.read_gz('price-reject-audit.jsonl.gz'));prices=json.loads((I/'price-ledgers.json').read_bytes());old=r.index(json.loads((I/'old-fresh-predictions.json').read_bytes())['predictions'])
 if len(labels)!=726 or not set(labels)<=set(source):raise ValueError('test label cohort mismatch')
 for fid,v in labels.items():
  s=source[fid]
  if any(v[k]!=s[k] for k in ('home_score','away_score')) or v['status']!='final' or any(type(v[k]) is not int or v[k]<0 for k in ('home_score','away_score')):raise ValueError('label mismatch')
 return pred,labels,prices,audit,old

def winner(y):return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
def forecast(p,winner,prices):
 v=p['probabilities_2way'];p2=[v['home'],v['away']];pick=('home','away')[int(p2[1]>p2[0])];void=winner=='draw';hit=not void and pick==winner;net=D(0) if void else D(str(prices[pick]))-1 if hit else D(-1)
 return {'probabilities':p2,'pick':pick,'hit':hit,'void':void,'net':str(net)}

def metric(ledger,name):
 net=D(0);hits=voids=0;monthly=collections.defaultdict(D);daily=collections.defaultdict(D);loss=brier=0.
 for x in ledger:
  f=x['forecasts'][name];z=D(f['net']);net+=z;monthly[x['date'][:7]]+=z;daily[x['date']]+=z;hits+=f['hit'];voids+=f['void']
  if not f['void']:
   y=0 if x['winner']=='home' else 1;loss-=math.log(max(f['probabilities'][y],1e-15));brier+=sum((p-(i==y))**2 for i,p in enumerate(f['probabilities']))
 n=len(ledger);dec=n-voids;balance=peak=dd=D(0)
 for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
 return {'bets':n,'hits':hits,'voids':voids,'decisive':dec,'accuracy_decisive':hits/dec if dec else None,'net_units':str(net),'roi_including_refunds':float(net/n) if n else None,'logloss_decisive':loss/dec if dec else None,'brier_decisive':brier/dec if dec else None,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'max_daily_close_drawdown_units':str(dd) if n else None}

def paired_price(ledger,a,b):
 groups=collections.defaultdict(lambda:np.zeros(4));changed=0
 for x in ledger:
  aa=x['forecasts'][a];bb=x['forecasts'][b];g=groups[date.fromisoformat(x['date']).isocalendar()[:2]]
  if not aa['void']:g[0]+=1;g[1]+=int(aa['hit'])-int(bb['hit'])
  g[2]+=1;g[3]+=float(D(aa['net'])-D(bb['net']));changed+=aa['pick']!=bb['pick']
 z={'weeks':len(groups),'changed':changed,'accuracy_delta_ci95':None,'roi_delta_ci95':None}
 if len(groups)>=2:
  arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
  if np.all(boot[:,0]>0):z['accuracy_delta_ci95']=np.quantile(boot[:,1]/boot[:,0],[.025,.975]).tolist()
  z['roi_delta_ci95']=np.quantile(boot[:,3]/boot[:,2],[.025,.975]).tolist()
 return z

def scenario_score(name,pred,labels,prices,audit,old):
 families={k:r.index(v) for k,v in pred['scenarios'][name].items()};ids=set(families['M0']);
 if len(ids)!=709 or any(set(v)!=ids for v in families.values()) or not ids<=set(labels) or not ids<=set(old):raise ValueError('scenario cohort mismatch')
 oldname='original_projected' if name=='projected' else 'official_batting_and_starter';families['old_fixed']={fid:old[fid]['settings'][oldname] for fid in ids};# The frozen team comparator is available on the fixed price ledger.
 results={'threeway':outcome4(families,labels,ids),'prices':{}};ledgers={}
 for pc,source in prices['ledgers'].items():
  ledger=[]
  for x in source:
   fid=x['fixture_id']
   if fid not in ids:continue
   win=winner(labels[fid])
   if win!=x['winner']:raise ValueError('price label mismatch')
   forecasts={k:forecast(families[k][fid],win,x['price_source']['prices']) for k in ('M0','M1','M2','old_fixed')};forecasts.update({k:x['forecasts'][k] for k in ('team','market')});rejected=x['price_source']['origin']!='official_restored' and audit[fid]['official_result']['buyReject']!='0';ledger.append({'fixture_id':fid,'date':x['date'],'winner':win,'price_source':x['price_source'],'current_buy_reject':bool(rejected),'forecasts':forecasts})
  sensitiv=[x for x in ledger if not x['current_buy_reject']]
  for label,ls in [(pc,ledger),(pc+'_current_buy_reject_excluded',sensitiv)]:
   results['prices'][label]={'n':len(ls),'refunds':sum(x['winner']=='draw' for x in ls),'excluded_current_reject':len(ledger)-len(ls),'models':{k:metric(ls,k) for k in NAMES},'versus_M0':{k:paired_price(ls,k,'M0') for k in ('M1','M2','old_fixed','team','market')},'versus_market':{k:paired_price(ls,k,'market') for k in ('M0','M1','M2','old_fixed')}};ledgers[label]=ls
 return results,ledgers

def outcome4(predictions,labels,ids):
 names=('M0','M1','M2','old_fixed');total={k:np.zeros(3) for k in names};groups={k:collections.defaultdict(lambda:np.zeros(4)) for k in names if k!='M0'}
 for fid in sorted(ids,key=lambda fid:(predictions['M0'][fid]['event_date'],fid)):
  truth=winner(labels[fid]);ms={}
  for name in names:
   p=predictions[name][fid]['probabilities_3way'];ms[name]=np.array([int(max(('home','draw','away'),key=p.get)==truth),-math.log(max(p[truth],1e-15)),sum((p[k]-(k==truth))**2 for k in ('home','draw','away'))]);total[name]+=ms[name]
  w=date.fromisoformat(predictions['M0'][fid]['event_date']).isocalendar()[:2]
  for name in groups:groups[name][w]+=np.r_[1,ms[name]-ms['M0']]
 n=len(ids);out={'n':n,'models':{k:{'hits':int(v[0]),'accuracy':float(v[0]/n),'logloss':float(v[1]/n),'brier':float(v[2]/n)} for k,v in total.items()},'versus_M0':{}}
 for name,buckets in groups.items():
  arr=np.array([buckets[k] for k in sorted(buckets)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1);out['versus_M0'][name]={'weeks':len(arr),**{k:np.quantile(boot[:,i]/boot[:,0],[.025,.975]).tolist() for i,k in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1)}}
 return out

def main():
 pred,labels,prices,audit,old=read_pinned();results={};ledgers={}
 for scenario in r.SETTINGS:results[scenario],ledgers[scenario]=scenario_score(scenario,pred,labels,prices,audit,old)
 for s in r.SETTINGS:
  p=results[s]['prices'];assert p['extended_day_team_source_confirmed']['n']==667 and p['strict_original_time_match']['n']==439 and p['extended_day_team_source_confirmed_current_buy_reject_excluded']['n']==660
 expected={'projected':(398,-.09625187406296852),'official_batting_and_starter':(403,-.08262368815592204)}
 for s,(hits,roi) in expected.items():
  assert results[s]['threeway']['models']['old_fixed']['hits']==hits and abs(results[s]['prices']['extended_day_team_source_confirmed']['models']['old_fixed']['roi_including_refunds']-roi)<1e-10
 r.dump(A/'results.json',{'prediction_freeze':FREEZE,'prediction_sha256':SHA,'selected_family':pred['selected_family'],'scenarios':results,'price_ledgers':ledgers,'old_fixed_is_not_new_M0':True,'current_buy_reject_not_historic_sellability':True,'historical_receipt_verified':False});print(json.dumps({s:{'outcome':x['threeway']['models'],'extended':{k:v['roi_including_refunds'] for k,v in x['prices']['extended_day_team_source_confirmed']['models'].items()}} for s,x in results.items()}))
if __name__=='__main__':main()
