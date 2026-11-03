"""Independent M2-v2 quality reconstruction and Decimal settlement audit."""
import gzip,importlib.util,json,math,sys
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P));import research_v2 as v2
r=v2.v1
spec=importlib.util.spec_from_file_location('_v2_independent_v1',P.parent/'independent_verify.py');ind=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ind;spec.loader.exec_module(ind)
def manual(row,source,scenario,model):
 s=model['spec'];n=model['nbase'];oldbase=n-12;d=n+len(s['hitters'])+len(s['starters']);beta=model['beta'];split=[beta[k*d:(k+1)*d] for k in range(2)];trim=[b[:oldbase]+b[n:] for b in split];base_model={'spec':s,'nbase':oldbase,'beta':trim[0]+trim[1],'family':'M2'};p,_=ind.manual(row,source,scenario,base_model);logits=[math.log(p[0]/p[2]),math.log(p[1]/p[2])];missing=[];quality=[];hm=set(s['hitters']);sm=set(s['starters']);roles={side:ind.roster(source,scenario,side) for side in ('home','away')}
 for side,opp in (('home','away'),('away','home')):
  _,flags=ind.vector(row,source,side,s);missing.append(flags);hitters=roles[side][0];pitcher=roles[opp][1];quality.append([float(hitters is None),sum(x not in hm for x in hitters)/9 if hitters else 0.,float(pitcher is None),float(pitcher is not None and pitcher not in sm)])
 q=[a+b for a,b in zip(*missing)]+[a+b for a,b in zip(*quality)]
 logits=[logits[k]+sum(a*b for a,b in zip(split[k][oldbase:n],q)) for k in range(2)]+[0.];top=max(logits);x=[math.exp(z-top) for z in logits];return [z/sum(x) for z in x]

def main():
 model=json.loads((P/'model.json').read_bytes())['model'];pred=json.loads((P/'predictions.json').read_bytes());res=json.loads((P/'results.json').read_bytes());base=ind.gz(r.I/'features.jsonl.gz');labels=ind.gz(r.I/'test-labels.jsonl.gz');maxerr=0.;checked=0
 for scenario,items in pred['scenarios'].items():
  sources=ind.gz(r.I/('fresh-history-2026-'+scenario+'.jsonl.gz'))
  for x in items:
   p=manual(base[x['fixture_id']],sources[x['fixture_id']],scenario,model);maxerr=max(maxerr,*[abs(p[i]-x['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))])
  hit=0;loss=brier=0.
  for x in items:
   y=labels[x['fixture_id']];truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';q=x['probabilities_3way'];hit+=max(('home','draw','away'),key=q.get)==truth;loss-=math.log(q[truth]);brier+=sum((q[k]-(k==truth))**2 for k in ('home','draw','away'))
  metric=res['scenarios'][scenario]['threeway']['models']['M2'];assert hit==metric['hits'] and abs(loss/len(items)-metric['logloss'])<1e-12 and abs(brier/len(items)-metric['brier'])<1e-12
  for tier,ledger in res['price_ledgers'][scenario].items():
   for name in ('M2','M2_v1'):
    net=Decimal(0);hits=voids=0;daily={};monthly={}
    for x in ledger:
     y=labels[x['fixture_id']];win='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';f=x['forecasts'][name];pick=('home','away')[int(f['probabilities'][1]>f['probabilities'][0])];hit=win==pick;void=win=='draw';value=Decimal(0) if void else Decimal(str(x['price_source']['prices'][pick]))-1 if hit else Decimal(-1);assert f['pick']==pick and Decimal(f['net'])==value;net+=value;hits+=hit;voids+=void;daily[x['date']]=daily.get(x['date'],Decimal(0))+value;month=x['date'][:7];monthly[month]=monthly.get(month,Decimal(0))+value
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    metrics=res['scenarios'][scenario]['prices'][tier]['models']['M2'] if name=='M2' else res['scenarios'][scenario]['prices'][tier]['M2_v1'];assert net==Decimal(metrics['net_units']) and hits==metrics['hits'] and voids==metrics['voids'] and Decimal(metrics['max_daily_close_drawdown_units'])==dd and {k:Decimal(v) for k,v in metrics['monthly_units'].items()}==monthly;checked+=len(ledger)
 if maxerr>1e-10:raise ValueError('independent v2 probability mismatch')
 print(json.dumps({'independent_predictions':1418,'max_probability_error':maxerr,'decimal_settlements':checked,'status':'PASS'}))
if __name__=='__main__':main()
