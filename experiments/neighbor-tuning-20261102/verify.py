"""Independent standard-library replay of tuning, predictions and settlements."""
import json,math,itertools
from datetime import date
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent
rows=[json.loads(x) for x in (P/'inputs/oof.jsonl').read_text(encoding='utf8').splitlines()]
report=json.loads((P/'results.json').read_text(encoding='utf8'))
ledger=[json.loads(x) for x in (P/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
hist=[r for r in rows if r['split']=='meta_history_2024']; train=[r for r in hist if r['day']<'2024-07-25'];val=[r for r in hist if '2024-08-01'<=r['day']<'2025-01-01'];test={r['id']:r for r in rows if r['split']=='evaluation_2025'}
configs=list(itertools.product(('market','combined'),('uniform','distance'),(30,60),(30,120)))
def probs(bank,r,c):
    space,weight,k,a=c
    def vec(v):return v['market_p']+(v['dc_p'] if space=='combined' else [])
    target=vec(r)
    near=sorted((math.sqrt(sum((x-y)**2 for x,y in zip(vec(z),target))),z['id'],z) for z in bank)[:k]
    weights=[1 if weight=='uniform' else 1/(d+0.05) for d,_,_ in near];scale=k/sum(weights)
    p=[max(1e-6,r['market_p'][j]+sum(w*scale*((z['y']==j)-z['market_p'][j]) for w,(_,_,z) in zip(weights,near))/(k+a)) for j in range(3)]
    return [x/sum(p) for x in p]
def score(pred):return (sum(max(range(3),key=lambda i:p[i])==r['y'] for p,r in zip(pred,val)),sum(-math.log(p[r['y']]) for p,r in zip(pred,val))/len(val))
market=score([r['market_p'] for r in val]);eligible=[]
for i,c in enumerate(configs):
    hits,loss=score([probs(train,r,c) for r in val]);saved=report['validation_candidates'][i]
    assert saved['config']==list(c) and saved['hits']==hits and abs(saved['logloss']-loss)<1e-12
    if hits>market[0] and loss<=market[1]:eligible.append((-hits,loss,i,c))
choice=min(eligible)[3] if eligible else None
assert (list(choice) if choice else 'market')==report['selected']
maxerr=0;settlements=0
for line in ledger:
    r=test[line['id']]; assert line['result']==r['y'] and line['prices']==r['prices']
    target=r['market_p'];expected=probs(hist,r,choice) if choice else target
    assert line['forecasts']['market']['probability']==r['market_p'] and line['forecasts']['dc']['probability']==r['dc_p']
    def v(z): return z['market_p']+z['dc_p']
    neighbors=sorted((sum((x-y)**2 for x,y in zip(v(z),v(r))),z['id'],z['y']) for z in hist)[:60]
    original=[(1+sum(y==j for _,_,y in neighbors))/63 for j in range(3)]
    assert all(abs(a-b)<1e-12 for a,b in zip(original,line['forecasts']['original']['probability']))
    assert line['forecasts']['home']['probability']==[1.0,0.0,0.0]
    for x,y in zip(expected,line['forecasts']['tuned']['probability']):maxerr=max(maxerr,abs(x-y))
    assert maxerr<1e-12
    for v in line['forecasts'].values():
        p=v['probability']; pick=max(range(3),key=lambda i:p[i]);hit=pick==r['y'];net=Decimal(r['prices'][pick])-1 if hit else Decimal(-1)
        assert v['pick']==pick and v['hit']==hit and Decimal(v['net'])==net
        settlements+=1
print(json.dumps({'validation_configurations_replayed':len(configs),'probability_replays':len(ledger),'decimal_settlements':settlements,'max_probability_error':maxerr}))
