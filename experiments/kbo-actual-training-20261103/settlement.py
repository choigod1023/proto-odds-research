"""Common-cohort score and refund settlement for six frozen model cells."""
import collections,math
from datetime import date
from decimal import Decimal
import numpy as np
import pipeline as p

def outcome(labels,fid):
 y=labels[fid];h=y['home_score'];a=y['away_score']
 if y.get('status')!='final' or any(type(v) is not int or v<0 for v in (h,a)):raise ValueError('invalid final result')
 return 'home' if h>a else 'away' if h<a else 'draw'

def metric(rows,name):
 n=len(rows);hits=void=0;net=Decimal(0);daily=collections.defaultdict(Decimal);months=collections.defaultdict(Decimal);loss=brier=0.
 for row in rows:
  f=row['forecasts'][name];value=Decimal(f['net']);net+=value;hits+=f['hit'];void+=f['void'];daily[row['date']]+=value;months[row['date'][:7]]+=value
  if not f['void']:
   y=0 if row['winner']=='home' else 1;loss-=math.log(max(f['probabilities'][y],1e-15));brier+=sum((x-(i==y))**2 for i,x in enumerate(f['probabilities']))
 total=peak=dd=Decimal(0)
 for day in sorted(daily):total+=daily[day];peak=max(peak,total);dd=max(dd,peak-total)
 dec=n-void
 return {'bets':n,'hits':hits,'voids':void,'decisive':dec,'accuracy_decisive':hits/dec if dec else None,'logloss_decisive':loss/dec if dec else None,'brier_decisive':brier/dec if dec else None,'net_units':str(net),'roi_including_refunds':float(net/n) if n else None,'monthly_units':{k:str(v) for k,v in sorted(months.items())},'max_daily_close_drawdown_units':str(dd) if n else None}

def paired(rows,first,second):
 grouped=collections.defaultdict(lambda:np.zeros(4));changed=0
 for row in rows:
  x=row['forecasts'][first];y=row['forecasts'][second];g=grouped[date.fromisoformat(row['date']).isocalendar()[:2]]
  if not x['void']:g[0]+=1;g[1]+=int(x['hit'])-int(y['hit'])
  g[2]+=1;g[3]+=float(Decimal(x['net'])-Decimal(y['net']));changed+=x['pick']!=y['pick']
 result={'weeks':len(grouped),'changed':changed,'accuracy_delta_ci95':None,'roi_delta_ci95':None}
 if len(grouped)>=2:
  arr=np.array([grouped[k] for k in sorted(grouped)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(1)
  if np.all(boot[:,0]>0):result['accuracy_delta_ci95']=np.quantile(boot[:,1]/boot[:,0],[.025,.975]).tolist()
  result['roi_delta_ci95']=np.quantile(boot[:,3]/boot[:,2],[.025,.975]).tolist()
 return result

def outcome_paired(predictions,labels,first,second):
 a=p.index(predictions[first]);b=p.index(predictions[second]);groups=collections.defaultdict(lambda:np.zeros(4));changed=0
 if set(a)!=set(b):raise ValueError('outcome cohorts differ')
 for fid in sorted(a,key=lambda z:(a[z]['event_date'],z)):
  truth=outcome(labels,fid);x=a[fid]['probabilities_3way'];y=b[fid]['probabilities_3way'];changed+=max(('home','draw','away'),key=x.get)!=max(('home','draw','away'),key=y.get)
  def score(q):return np.array([int(max(('home','draw','away'),key=q.get)==truth),-math.log(max(q[truth],1e-15)),sum((q[k]-(k==truth))**2 for k in ('home','draw','away'))])
  groups[date.fromisoformat(a[fid]['event_date']).isocalendar()[:2]]+=np.r_[1,score(x)-score(y)]
 result={'weeks':len(groups),'changed':changed,'accuracy_delta_ci95':None,'logloss_delta_ci95':None,'brier_delta_ci95':None}
 if len(groups)>=2:
  arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(1)
  for i,k in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1):result[k]=np.quantile(boot[:,i]/boot[:,0],[.025,.975]).tolist()
 return result

def settle(predictions,labels,price_ledgers,reject_audit):
 if set(predictions)!=set(p.MODEL_NAMES):raise ValueError('missing model cell')
 models={k:p.index(v) for k,v in predictions.items()};ids=set(models[p.MODEL_NAMES[0]])
 if not ids or any(set(x)!=ids for x in models.values()) or not ids<=set(labels):raise ValueError('unequal target cohorts or missing outcomes')
 for fid in ids:outcome(labels,fid)
 comparisons=(('M0_actual','M0_projected'),('M2_actual','M2_projected'),('M2_projected','M0_projected'),('M2_actual','M0_actual'),('oldfull_M0','M0_projected'),('oldfull_M2','M2_projected'))
 result={'outcome':p.diagnostics(predictions,labels),'outcome_paired':{a+'_vs_'+b:outcome_paired(predictions,labels,a,b) for a,b in comparisons},'price_cohorts':{}};ledgers={}
 for cohort,source in price_ledgers.items():
  rows=[];seen=set()
  for old in source:
   fid=old['fixture_id']
   if fid not in ids:continue
   if fid in seen:raise ValueError('duplicate price fixture')
   seen.add(fid);win=outcome(labels,fid)
   if win!=old['winner']:raise ValueError('fixed price outcome mismatch')
   price=old['price_source']['prices'];forecasts={}
   for name,ps in models.items():
    v=ps[fid]['probabilities_2way'];vec=[v['home'],v['away']];pick=('home','away')[int(vec[1]>vec[0])];void=win=='draw';hit=not void and pick==win;net=Decimal(0) if void else Decimal(str(price[pick]))-1 if hit else Decimal(-1);forecasts[name]={'probabilities':vec,'pick':pick,'void':void,'hit':hit,'net':str(net)}
   if old['price_source']['origin']=='official_restored':reject=False
   else:
    if fid not in reject_audit:raise ValueError('missing current reject audit')
    reject=reject_audit[fid]['official_result']['buyReject']!='0'
   rows.append({'fixture_id':fid,'date':old['date'],'winner':win,'price_source':old['price_source'],'current_buy_reject':reject,'forecasts':forecasts})
  for suffix,ledger in (('',rows),('_current_reject_excluded',[x for x in rows if not x['current_buy_reject']])):
   key=cohort+suffix;result['price_cohorts'][key]={'n':len(ledger),'target_denominator':len(ids),'excluded_current_reject':len(rows)-len(ledger),'refunds':sum(x['winner']=='draw' for x in ledger),'models':{k:metric(ledger,k) for k in p.MODEL_NAMES},'paired':{a+'_vs_'+b:paired(ledger,a,b) for a,b in comparisons}};ledgers[key]=ledger
 return result,ledgers
