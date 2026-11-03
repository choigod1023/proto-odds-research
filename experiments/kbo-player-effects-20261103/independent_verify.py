"""Independent dense coefficients, Poisson convolution, and Decimal audit."""
import collections,gzip,json,math,hashlib,subprocess
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent;I=P/'inputs';A=P/'artifacts';W=[x/4.3 for x in (4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9)]
def gz(path):return {x['fixture_id']:x for x in (json.loads(z) for z in gzip.decompress(path.read_bytes()).splitlines())}
def scalar(name,value):
 if value is None or name=='park_factor':return None
 if name.endswith('_rest_days'):return float(value) if type(value) is int and value>=0 else None
 if name.endswith('_team_strength'):
  a=[x['runs_for']-x['runs_against'] for x in value if all(type(x.get(k)) is int and x[k]>=0 for k in ('runs_for','runs_against'))];return sum(a)/len(a) if a else None
 if name.endswith('_lineup_strength'):
  obs=[x for p in value for x in p['history'] or [] if all(type(x.get(k)) is int and x[k]>=0 for k in ('ab','hit','bb')) and x['hit']<=x['ab']];den=sum(x['ab']+x['bb'] for x in obs);return sum(x['hit']+x['bb'] for x in obs)/den if den else None
 if name.endswith('_starter_strength'):
  obs=[x for x in value['appearances'] if all(type(x.get(k)) is int and x[k]>=0 for k in ('er','outs'))];outs=sum(x['outs'] for x in obs);return 27*sum(x['er'] for x in obs)/outs if outs else None
 games=value['games'];return float(sum(x['relief']['outs'] for x in games)) if games and all(type(x['relief']['outs']) is int and x['relief']['outs']>=0 for x in games) else None

def vector(row,src,side,spec):
 other='away' if side=='home' else 'home';keys=(side+'_team_strength',other+'_team_strength',side+'_rest_days',other+'_rest_days','park_factor',side+'_lineup_strength',other+'_starter_strength',other+'_bullpen_load');raw=[scalar(k,src['features'][k]['value']) for k in keys];missing=[float(x is None) for x in raw];norm=[(x-spec['mean'][i])/spec['scale'][i] if x is not None else 0. for i,x in enumerate(raw)];return norm,missing

def roster(src,scenario,side):
 if scenario=='projected':return src['projection'][side+'_lineup']['player_ids'],src['projection'][side+'_starter']['player_id']
 return [p['player_id'] for p in src['official_lineup_context']['lineups'][side]],src['official_schedule_pitcher_context']['pitchers'][side]['player_id']

def manual(row,src,scenario,model):
 spec=model['spec'];team=spec['teams'];hitters=spec['hitters'];starters=spec['starters'];hm={k:i for i,k in enumerate(hitters)};sm={k:i for i,k in enumerate(starters)};d=model['nbase']+(len(hitters)+len(starters) if model['family']!='M0' else 0);beta=model['beta'];rol={s:roster(src,scenario,s) for s in ('home','away')};num={s:vector(row,src,s,spec) for s in rol};flags={}
 for s,o in (('home','away'),('away','home')):
  h=rol[s][0];p=rol[o][1];flags[s]=[float(h is None),sum(x not in hm for x in h)/9 if h else 0.,float(p is None),float(p is not None and p not in sm)]
 if model['family'] in ('M0','M1'):
  rates=[]
  for s,o in (('home','away'),('away','home')):
   base=[1.,float(s=='home')]+[float(row[s+'_id']==x) for x in team]+[float(row[o+'_id']==x) for x in team]+num[s][0]+num[s][1]
   if model['family']=='M1':base+=flags[s]
   z=sum(a*b for a,b in zip(base,beta))
   if model['family']=='M1':
    for j,p in enumerate(rol[s][0] or []):
     if p in hm:z+=W[j]*beta[len(base)+hm[p]]
    p=rol[o][1]
    if p in sm:z+=beta[len(base)+len(hitters)+sm[p]]
   rates.append(math.exp(z))
  h,a=rates;ph=[math.exp(-h)];pa=[math.exp(-a)]
  for j in range(1,100):ph.append(ph[-1]*h/j);pa.append(pa[-1]*a/j)
  p=[0.,0.,0.]
  for i,hi in enumerate(ph):
   for j,aj in enumerate(pa):p[0 if i>j else 1 if i==j else 2]+=hi*aj
  return p,rates
 base=[1.]+[float(row['home_id']==t)-float(row['away_id']==t) for t in team]+[a-b for a,b in zip(num['home'][0],num['away'][0])]+[a-b for a,b in zip(num['home'][1],num['away'][1])]+[a-b for a,b in zip(flags['home'],flags['away'])];logits=[]
 for k in range(2):
  coeff=beta[k*d:(k+1)*d];z=sum(a*b for a,b in zip(base,coeff))
  for side,sign in [('home',1),('away',-1)]:
   for j,p in enumerate(rol[side][0] or []):
    if p in hm:z+=sign*W[j]*coeff[len(base)+hm[p]]
   p=rol[side][1]
   if p in sm:z+=sign*coeff[len(base)+len(hitters)+sm[p]]
  logits.append(z)
 logits.append(0.);peak=max(logits);v=[math.exp(x-peak) for x in logits];return [x/sum(v) for x in v],None

def main():
 models=json.loads((A/'final-models.json').read_bytes())['models'];pred=json.loads((A/'predictions.json').read_bytes());result=json.loads((A/'results.json').read_bytes());base=gz(I/'features.jsonl.gz');maxprob=maxrate=0.
 for scenario,allpred in pred['scenarios'].items():
  sources=gz(I/('fresh-history-2026-'+scenario+'.jsonl.gz'))
  for name,items in allpred.items():
   for x in items:
    p,rates=manual(base[x['fixture_id']],sources[x['fixture_id']],scenario,models[name]);maxprob=max(maxprob,*[abs(p[i]-x['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))]);
    if rates:maxrate=max(maxrate,abs(rates[0]-x['home_rate']),abs(rates[1]-x['away_rate']))
 if maxprob>1e-9 or maxrate>1e-10:raise ValueError('independent predictions mismatch')
 labels=gz(I/'test-labels.jsonl.gz');checks=0
 old={x['fixture_id']:x for x in json.loads((I/'old-fresh-predictions.json').read_bytes())['predictions']}
 for scenario,sets in pred['scenarios'].items():
  old_name='original_projected' if scenario=='projected' else 'official_batting_and_starter'
  sets={**sets,'old_fixed':[{'fixture_id':x['fixture_id'],**old[x['fixture_id']]['settings'][old_name]} for x in sets['M0']]}
  for name,items in sets.items():
   hit=0;loss=brier=0.
   for x in items:
    y=labels[x['fixture_id']];t='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';q=x['probabilities_3way'];hit+=max(('home','draw','away'),key=q.get)==t;loss-=math.log(q[t]);brier+=sum((q[k]-(k==t))**2 for k in ('home','draw','away'))
   m=result['scenarios'][scenario]['threeway']['models'][name];assert hit==m['hits'] and abs(loss/len(items)-m['logloss'])<1e-12 and abs(brier/len(items)-m['brier'])<1e-12
 for scenario,tiers in result['price_ledgers'].items():
  for tier,ledger in tiers.items():
   for name,m in result['scenarios'][scenario]['prices'][tier]['models'].items():
    net=Decimal(0);hit=void=0;daily={};monthly={}
    for x in ledger:
     y=labels[x['fixture_id']];winner='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';f=x['forecasts'][name];pick=('home','away')[int(f['probabilities'][1]>f['probabilities'][0])];w=winner==pick;v=winner=='draw';z=Decimal(0) if v else Decimal(str(x['price_source']['prices'][pick]))-1 if w else Decimal(-1);assert f['pick']==pick and Decimal(f['net'])==z;net+=z;hit+=w;void+=v;daily[x['date']]=daily.get(x['date'],Decimal(0))+z;month=x['date'][:7];monthly[month]=monthly.get(month,Decimal(0))+z
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    assert net==Decimal(m['net_units']) and hit==m['hits'] and void==m['voids'] and {k:Decimal(v) for k,v in m['monthly_units'].items()}==monthly and Decimal(m['max_daily_close_drawdown_units'])==dd and abs(float(net)/len(ledger)-m['roi_including_refunds'])<1e-12;checks+=len(ledger)
 print(json.dumps({'independent_predictions':4254,'max_probability_error':maxprob,'max_rate_error':maxrate,'decimal_settlements':checks,'status':'PASS'}))
if __name__=='__main__':main()
