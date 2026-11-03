"""Independent manual scalar/model and Decimal settlement audit."""
import gzip,importlib.util,json,math,sys
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,P/path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
f=module('_fresh_verify_predict','fresh_history_predict.py');ind=module('_fresh_independent_formula','factorial_independent_verify.py')
def independent_scalar(key,value):
 if value is None:return None
 if 'lineup' in key:
  hs=[h for p in value for h in p['history'] or [] if all(type(h.get(k)) is int and h[k]>=0 for k in ('ab','hit','bb')) and h['hit']<=h['ab']];den=sum(h['ab']+h['bb'] for h in hs);return sum(h['hit']+h['bb'] for h in hs)/den if den else None
 if 'starter' in key:
  hs=[h for h in value['appearances'] if all(type(h.get(k)) is int and h[k]>=0 for k in ('er','outs'))];outs=sum(h['outs'] for h in hs);return 27*sum(h['er'] for h in hs)/outs if outs else None
 games=value['games'];return float(sum(g['relief']['outs'] for g in games)) if games and all(type(g['relief']['outs']) is int and g['relief']['outs']>=0 for g in games) else None

def main():
 x,source,old,manifest=f.load();base=f.m.index(x['features']);model=x['predictions']['models']['player'];fresh=json.loads((P/'fresh-history/predictions.json').read_bytes());result=json.loads((P/'fresh-history/results.json').read_bytes());labels=ind.rows(P/'phase2/test-labels.jsonl.gz');fs={v['fixture_id']:v for v in fresh['predictions']};os={v['fixture_id']:v for v in old['predictions']};maxrate=maxprob=0.
 for fid,rec in fs.items():
  for src,name in f.NAMES.items():
   row={**base[fid],'features':dict(base[fid]['features'])}
   for key in f.PLAYER:row['features'][key]={'value':independent_scalar(key,source[src][fid]['features'][key]['value'])}
   h=ind.rate(row,'home',model);a=ind.rate(row,'away',model);p=rec['settings'][name];maxrate=max(maxrate,abs(h-p['home_rate']),abs(a-p['away_rate']));dist=ind.dist(h,a);maxprob=max(maxprob,*[abs(dist[i]-p['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))])
 if maxrate>1e-10 or maxprob>1e-9:raise ValueError('independent prediction mismatch')
 groups={'full_709':set(os),'original_193':{k for k,v in os.items() if v['cohort']=='original_193'},'new_516':{k for k,v in os.items() if v['cohort']=='new_complement'},'through_2026_07_26':{k for k,v in os.items() if v['event_date']<='2026-07-26'},'after_2026_07_26':{k for k,v in os.items() if v['event_date']>'2026-07-26'}}
 names=tuple('old_'+k for k in f.NAMES.values())+tuple('fresh_'+k for k in f.NAMES.values())+('team',)
 for group,ids in groups.items():
  metrics=result['results'][group]['threeway']['models']
  for name in names:
   hits=0;loss=brier=0.
   for fid in ids:
    y=labels[fid];truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';pred=os[fid]['frozen_team'] if name=='team' else (fs[fid] if name.startswith('fresh_') else os[fid])['settings'][name.split('_',1)[1]];p=pred['probabilities_3way'];hits+=max(('home','draw','away'),key=p.get)==truth;loss-=math.log(p[truth]);brier+=sum((p[k]-(k==truth))**2 for k in ('home','draw','away'))
   m=metrics[name];assert hits==m['hits'] and abs(loss/len(ids)-m['logloss_3way'])<1e-12 and abs(brier/len(ids)-m['brier_3way'])<1e-12
  for cohort,entries in result['price_ledgers'][group].items():
   for name in names+('market',):
    net=Decimal(0);hits=voids=0;daily={};monthly={}
    for e in entries:
     probs=e['forecasts'][name]['probabilities'];pick=('home','away')[max(range(2),key=lambda i:probs[i])];win=e['winner'];void=win=='draw';hit=win==pick;z=Decimal(0) if void else Decimal(str(e['price_source']['prices'][pick]))-1 if hit else Decimal(-1);net+=z;hits+=hit;voids+=void;daily[e['date']]=daily.get(e['date'],Decimal(0))+z;month=e['date'][:7];monthly[month]=monthly.get(month,Decimal(0))+z
    bal=peak=dd=Decimal(0)
    for d in sorted(daily):bal+=daily[d];peak=max(peak,bal);dd=max(dd,peak-bal)
    m=result['results'][group]['prices'][cohort]['models'][name];assert net==Decimal(m['net_units']) and hits==m['hits'] and voids==m['voids'] and {k:Decimal(v) for k,v in m['monthly_units'].items()}==monthly
    if entries:assert Decimal(m['max_daily_close_drawdown_units'])==dd and abs(float(net)/len(entries)-m['roi_including_refunds'])<1e-12
    else:assert m['max_daily_close_drawdown_units'] is None and m['roi_including_refunds'] is None
 print(json.dumps({'independent_fresh_predictions':len(fs)*4,'max_rate_error':maxrate,'max_threeway_error':maxprob,'outcome_groups':len(groups),'price_groups':10,'decimal_drawdown_monthly_refunds':'passed'}))
if __name__=='__main__':main()
