"""Independent scalar audit of the selected residual, rather than all ML refits."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
import json,math
from pathlib import Path

HERE=Path(__file__).resolve().parent


def read(path):return [json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]


def one(bank,r):
    def v(x):return x['market_p']+x['dc_p']+x['features']['form12']
    values=[v(x) for x in bank];mean=[sum(x[j] for x in values)/len(values) for j in range(18)]
    sd=[math.sqrt(sum((x[j]-mean[j])**2 for x in values)/len(values)) or 1 for j in range(18)]
    target=[(x-mean[j])/sd[j] for j,x in enumerate(v(r))]
    pairs=[]
    for b,x in zip(bank,values):
        d=sum(((x[j]-mean[j])/sd[j]-target[j])**2 for j in range(18))
        pairs.append((d,b['id'],b))
    near=[x[2] for x in sorted(pairs)[:30]]
    p=[max(1e-6,r['market_p'][j]+sum(int(b['y']==j)-b['market_p'][j] for b in near)/40) for j in range(3)]
    return [x/sum(p) for x in p]


def settle(p,y,prices):
    pick=max(range(3),key=lambda j:p[j]);hit=pick==y
    return int(hit),Decimal(prices[pick])-1 if hit else Decimal(-1)


def verify():
    selected=json.loads((HERE/'development/selected-model.json').read_text())
    assert selected['config']=={'id':'residual:form:k=30:prior=10:weight=uniform:half=None:blend=1.0',
      'family':'residual','space':'form','k':30,'prior':10,'weight':'uniform','half_life':None,'blend':1.}
    trials=read(HERE/'development/trials.jsonl');priority={'market':0,'dc':1,'pool':2,'residual':3,'logistic':4,'random_forest':5,'hist_gradient_boosting':6}
    assert len(trials)==4334 and len({x['id'] for x in trials})==4334
    valid=[x for x in trials if x['status']=='ok']
    ranked=min(valid,key=lambda x:(-x['metrics']['hits'],x['metrics']['logloss'],priority[x['config']['family']],x['id']))
    assert ranked['config']==selected['config'] and ranked['metrics']==selected['development_metrics']
    data=read(HERE/'prepared/development.jsonl');cache={r['id']:r for r in read(HERE/'development/selected-validation.jsonl')}
    checked=0;error=0.;hits=0;net=Decimal(0)
    for fold in json.loads((HERE/'development/folds.json').read_text()):
        train=[r for r in data if r['day']<=fold['cutoff']]
        assert [r['id'] for r in train]==fold['training_ids']
        for r in (x for x in data if fold['start']<=x['day']<=fold['end']):
            p=one(train,r);error=max(error,max(abs(a-b) for a,b in zip(p,cache[r['id']]['p'])))
            h,n=settle(p,r['y'],r['prices']);hits+=h;net+=n;checked+=1
    assert checked==227 and hits==selected['development_metrics']['hits'] and net==Decimal(selected['development_metrics']['net_units'])
    target={r['id']:r for r in read(HERE/'prepared/outer-features.jsonl')};labels={r['id']:r['y'] for r in read(HERE/'prepared/outer-labels.jsonl')}
    predictions=read(HERE/'outer-predictions/predictions.jsonl');totals=defaultdict(lambda:[0,Decimal(0)]);settlements=0
    for row in predictions:
        r=target[row['id']];expected={'market':r['market_p'],'dc':r['dc_p'],selected['config']['id']:one(data,r)}
        for name,p in expected.items():
            error=max(error,max(abs(a-b) for a,b in zip(p,row['predictions'][name])))
            h,n=settle(p,labels[r['id']],r['prices']);totals[name][0]+=h;totals[name][1]+=n;settlements+=1
    result=json.loads((HERE/'outer-results/results.json').read_text())
    for name,(h,n) in totals.items():
        assert h==result['metrics'][name]['hits'] and n==Decimal(result['metrics'][name]['net_units'])
    assert error<1e-12 and len(predictions)==77 and settlements==231
    return {'trial_ranking_checked':len(valid),'all_trials_independently_refit':False,'development_selected_forecasts':checked,
      'development_hits':hits,'development_net':str(net),'outer_probability_vectors':settlements,
      'outer_decimal_settlements':settlements,'maximum_probability_error':error,
      'outer':{k:{'hits':v[0],'net':str(v[1])} for k,v in totals.items()}}


if __name__=='__main__':print(json.dumps(verify(),sort_keys=True))
