"""Independent dense prediction and Decimal settlement rederivation."""
import math
from decimal import Decimal
WEIGHTS=[x/4.3 for x in (4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9)]

def raw_side(row,side):
 opp='away' if side=='home' else 'home';keys=(side+'_team_strength',opp+'_team_strength',side+'_rest_days',opp+'_rest_days','park_factor',side+'_lineup_strength',opp+'_starter_strength',opp+'_bullpen_load');return [row['features'][k]['value'] for k in keys]
def probs_poisson(h,a):
 home=[math.exp(-h)];away=[math.exp(-a)]
 for i in range(1,100):home.append(home[-1]*h/i);away.append(away[-1]*a/i)
 out=[0.,0.,0.]
 for i,x in enumerate(home):
  for j,y in enumerate(away):out[0 if i>j else 1 if i==j else 2]+=x*y
 return out

def predict(model,row):
 spec=model['spec'];teams=spec['teams'];hitters=spec['hitters'];starters=spec['starters'];hm={x:i for i,x in enumerate(hitters)};sm={x:i for i,x in enumerate(starters)};num={};missing={};quality={}
 for side,opp in (('home','away'),('away','home')):
  raw=raw_side(row,side);num[side]=[(x-spec['mean'][i])/spec['scale'][i] if x is not None else 0. for i,x in enumerate(raw)];missing[side]=[float(x is None) for x in raw];line=row['roster'][side]['hitters'];starter=row['roster'][opp]['starter'];quality[side]=[float(line is None),sum(x not in hm for x in line)/9 if line else 0.,float(starter is None),float(starter is not None and starter not in sm)]
 beta=model['beta'];family=model['family']
 if family=='M0':
  rates=[]
  for side,opp in (('home','away'),('away','home')):
   base=[1.,float(side=='home')]+[float(row[side+'_id']==x) for x in teams]+[float(row[opp+'_id']==x) for x in teams]+num[side]+missing[side];rates.append(math.exp(sum(a*b for a,b in zip(base,beta))))
  return probs_poisson(*rates),rates
 if family!='M2_v2':raise ValueError('unexpected model family')
 base=[1.]+[float(row['home_id']==x)-float(row['away_id']==x) for x in teams]+[x-y for x,y in zip(num['home'],num['away'])]+[x-y for x,y in zip(missing['home'],missing['away'])]+[x-y for x,y in zip(quality['home'],quality['away'])]+[x+y for x,y in zip(missing['home'],missing['away'])]+[x+y for x,y in zip(quality['home'],quality['away'])];d=len(base)+len(hitters)+len(starters);logits=[]
 for cls in range(2):
  b=beta[cls*d:(cls+1)*d];z=sum(x*y for x,y in zip(base,b))
  for side,sign in (('home',1),('away',-1)):
   for i,player in enumerate(row['roster'][side]['hitters'] or []):
    if player in hm:z+=sign*WEIGHTS[i]*b[len(base)+hm[player]]
   starter=row['roster'][side]['starter']
   if starter in sm:z+=sign*b[len(base)+len(hitters)+sm[starter]]
  logits.append(z)
 logits.append(0.);high=max(logits);q=[math.exp(z-high) for z in logits];return [x/sum(q) for x in q],None

def verify_predictions(frozen,targets):
 max_prob=max_rate=0.;count=0
 for selector,models in frozen['predictions'].items():
  rows={x['fixture_id']:x for x in targets[selector]}
  for name,predictions in models.items():
   for x in predictions:
    q,rates=predict(frozen['models'][name],rows[x['fixture_id']]);max_prob=max(max_prob,*[abs(q[i]-x['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))]);count+=1
    if rates is not None:max_rate=max(max_rate,abs(rates[0]-x['home_rate']),abs(rates[1]-x['away_rate']))
 if max_prob>1e-9 or max_rate>1e-10:raise ValueError('independent prediction discrepancy')
 return {'predictions':count,'max_probability_error':max_prob,'max_rate_error':max_rate}

def verify_settlements(result):
 count=0
 for selector,tiers in result['ledgers'].items():
  for tier,rows in tiers.items():
   for name,ref in result['results'][selector]['price_cohorts'][tier]['models'].items():
    net=Decimal(0);hits=voids=0;daily={};monthly={}
    for row in rows:
     f=row['forecasts'][name];pick=('home','away')[int(f['probabilities'][1]>f['probabilities'][0])];win=row['winner'];void=win=='draw';hit=win==pick;z=Decimal(0) if void else Decimal(str(row['price_source']['prices'][pick]))-1 if hit else Decimal(-1)
     if f['pick']!=pick or Decimal(f['net'])!=z:raise ValueError('independent settlement discrepancy')
     net+=z;hits+=hit;voids+=void;daily[row['date']]=daily.get(row['date'],Decimal(0))+z;month=row['date'][:7];monthly[month]=monthly.get(month,Decimal(0))+z
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    if net!=Decimal(ref['net_units']) or hits!=ref['hits'] or voids!=ref['voids'] or {k:Decimal(v) for k,v in ref['monthly_units'].items()}!=monthly:raise ValueError('independent total mismatch')
    if rows and (Decimal(ref['max_daily_close_drawdown_units'])!=dd or abs(float(net)/len(rows)-ref['roi_including_refunds'])>1e-12):raise ValueError('independent ROI or drawdown mismatch')
    if not rows and (ref['max_daily_close_drawdown_units'] is not None or ref['roi_including_refunds'] is not None):raise ValueError('empty price cohort metric')
    count+=len(rows)
 return {'decimal_settlements':count}
