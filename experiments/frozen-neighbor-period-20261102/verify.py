"""Independent scalar reconstruction of every valid forecast and Decimal settlement."""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from src.devig import shin


def verify():
    raw=(HERE/'inputs/frozen-bank-source.jsonl').read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(raw).hexdigest()=='158cd931f3fa576b97678dfcc0b44042b9ef1a5826183853fb6d9b0beb314f05'
    bank=[json.loads(s) for s in raw.splitlines() if json.loads(s)['split']=='meta_history_2024']
    rows=[r for r in json.loads((HERE/'verified-inputs/domestic-opportunities.json').read_text(encoding='utf8')) if r['eligibility']=='eligible']
    ledger=[json.loads(s) for s in (HERE/'verified-results/domestic-ledger.jsonl').read_text(encoding='utf8').splitlines()]
    saved={r['id']:r for r in ledger}
    totals=defaultdict(lambda:{'hits':0,'net':Decimal(0),'loss':0.,'brier':0.})
    largest=0.;settlements=0
    residual=[sum(int(r['y']==j)-r['market_p'][j] for r in bank)/228 for j in range(3)]
    for r in rows:
        market=shin([float(v) for v in r['prices']]);x=list(market)+r['dc_p']
        near=sorted(bank,key=lambda b:(math.dist(b['market_p']+b['dc_p'],x),b['id']))[:60]
        before={'market':market,
          'global':[market[j]+(2/3)*residual[j] for j in range(3)],
          'neighbor':[market[j]+sum(int(b['y']==j)-b['market_p'][j] for b in near)/90 for j in range(3)]}
        y=0 if r['home_score']>r['away_score'] else 1 if r['home_score']==r['away_score'] else 2
        for name,p in before.items():
            p=[max(1e-6,v) for v in p];p=[v/sum(p) for v in p]
            s=saved[r['id']]['forecasts'][name]
            error=max(abs(a-b) for a,b in zip(p,s['p']));largest=max(largest,error)
            assert error<1e-12
            pick=max(range(3),key=lambda i:p[i]);hit=pick==y
            net=Decimal(r['prices'][pick])-1 if hit else Decimal(-1)
            assert (pick,hit,net)==(s['pick'],s['hit'],Decimal(s['net']))
            totals[name]['hits']+=hit;totals[name]['net']+=net
            totals[name]['loss']-=math.log(p[y]);totals[name]['brier']+=sum((v-int(j==y))**2 for j,v in enumerate(p))
            settlements+=1
    final=json.loads((HERE/'verified-results/results.json').read_text(encoding='utf8'))
    for name,t in totals.items():
        s=final['cohorts']['domestic']['models'][name]
        assert s['hits']==t['hits'] and Decimal(s['net_units'])==t['net']
        assert abs(s['logloss']-t['loss']/len(rows))<1e-12 and abs(s['brier']-t['brier']/len(rows))<1e-12
    assert final['cohorts']['overseas']['eligible']==0
    assert final['cohorts']['overseas']['excluded']=={'unverified_market_and_selection_order':120}
    initial=[json.loads(s) for s in (HERE/'domestic-ledger.jsonl').read_text(encoding='utf8').splitlines()]
    assert initial==ledger, 'eligibility correction must not silently retune domestic predictions'
    return {'verified_games':len(rows),'verified_probabilities':len(rows)*3,'verified_decimal_settlements':settlements,
      'maximum_probability_error':largest,'totals':{k:{'hits':v['hits'],'net':str(v['net'])} for k,v in totals.items()},
      'initial_domestic_unchanged':True,'unverified_overseas_excluded':120}


if __name__=='__main__':
    print(json.dumps(verify(),sort_keys=True))
