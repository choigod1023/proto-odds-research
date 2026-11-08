"""Independent pure-Python nearest-neighbor and Decimal settlement replay."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import hashlib,json,math
ROOT=Path(__file__).resolve().parent
records=[json.loads(x) for x in (ROOT/'inputs/oof.jsonl').read_text(encoding='utf8').splitlines()]
ledger=[json.loads(x) for x in (ROOT/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
result=json.loads((ROOT/'results.json').read_text(encoding='utf8'))
history=[r for r in records if r['split']=='meta_history_2024'];test={r['id']:r for r in records if r['split']=='evaluation_2025'}
assert len(ledger)==len(test)==227 and len({r['id'] for r in ledger})==227
sums={k:Decimal(0) for k in ('market','dc','neighbor')};hits=Counter();max_error=0.0
for saved in ledger:
    r=test[saved['id']];assert saved['day']==r['day'] and saved['result']==r['y'] and saved['prices']==r['prices']
    target=r['market_p']+r['dc_p'];rank=[]
    for t in history:
        v=t['market_p']+t['dc_p'];distance=sum((a-b)**2 for a,b in zip(target,v))
        rank.append((distance,t['id'],t['y']))
    rank.sort();k=result['selected_k'];counts=Counter(z[2] for z in rank[:k]);expected=[(counts[j]+1)/(k+3) for j in range(3)]
    for model in ('market','dc','neighbor'):
        f=saved['forecasts'][model];p={'market':r['market_p'],'dc':r['dc_p'],'neighbor':expected}[model]
        error=max(abs(a-b) for a,b in zip(p,f['probability']));max_error=max(max_error,error);assert error<1e-12
        pick=max(range(3),key=lambda i:(p[i],-i));hit=pick==r['y'];net=Decimal(r['prices'][pick])-1 if hit else Decimal(-1)
        assert f['pick']==pick and f['hit']==hit and Decimal(f['net'])==net
        sums[model]+=net;hits[model]+=hit
for model in sums:
    m=result['metrics'][model];assert Decimal(m['net_units'])==sums[model] and m['hits']==hits[model]
report={'independently_replayed_games':len(ledger),'decimal_settlements':len(ledger)*3,'max_probability_error':max_error,'input_sha256':hashlib.sha256((ROOT/'inputs/oof.jsonl').read_bytes().replace(b'\r\n',b'\n')).hexdigest()}
(ROOT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
print(json.dumps(report))
