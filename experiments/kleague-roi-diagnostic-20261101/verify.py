"""Independent probability replay and Decimal settlement; no DC/run imports."""
from collections import defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path

HERE=Path(__file__).resolve().parent


def independent_market(prices):
    """Solve Shin's sum-to-one equation using a rationalized independent form."""
    inverse=[1/float(x) for x in prices];total=sum(inverse)
    if total<=1:return [x/total for x in inverse]
    def values(z):
        return [(2*x*x/total)/(math.sqrt(z*z+4*(1-z)*x*x/total)+z) for x in inverse]
    lo,hi=1e-15,1-1e-12
    if sum(values(lo))<1 or sum(values(hi))>1:raise ValueError('unbracketed market equation')
    for _ in range(100):
        mid=(lo+hi)/2
        if sum(values(mid))>1:lo=mid
        else:hi=mid
    result=values((lo+hi)/2)
    return [x/sum(result) for x in result]


def verify():
    rows=[json.loads(s) for s in (HERE/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
    report=json.loads((HERE/'results.json').read_text(encoding='utf8'))
    states={s['reference_day'][:7]:s for s in json.loads((HERE/'states.json').read_text(encoding='utf8'))}
    assert len(rows)==len({r['id'] for r in rows})==227
    max_error=market_error=0.
    for r in rows:
        s=states[r['fit_month']];index={team:i for i,team in enumerate(s['teams'])}
        def effect(k,t):return s[k][index[t]] if t in index else 0.
        lh=max(.05,min(6.,math.exp(s['intercept']+s['home']+effect('attack',r['home_id'])+effect('defence',r['away_id']))))
        la=max(.05,min(6.,math.exp(s['intercept']+effect('attack',r['away_id'])+effect('defence',r['home_id']))))
        hp=[math.exp(-lh)];ap=[math.exp(-la)]
        for k in range(1,31):hp.append(hp[-1]*lh/k);ap.append(ap[-1]*la/k)
        p=[0.,0.,0.]
        for i in range(31):
            for j in range(31):
                correction=1.
                if (i,j)==(0,0):correction=1-lh*la*s['rho']
                elif (i,j)==(0,1):correction=1+lh*s['rho']
                elif (i,j)==(1,0):correction=1+la*s['rho']
                elif (i,j)==(1,1):correction=1-s['rho']
                p[0 if i>j else 1 if i==j else 2]+=hp[i]*ap[j]*correction
        p=[v/sum(p) for v in p]
        error=max(abs(a-b) for a,b in zip(p,r['forecasts']['time_weighted_dc']['p']))
        max_error=max(error,max_error);assert error<1e-12
        error=max(abs(a-b) for a,b in zip(independent_market(r['prices']),r['forecasts']['market']['p']))
        market_error=max(error,market_error);assert error<1e-10
        assert r['day']>=s['reference_day'] and r['training_cutoff']<s['reference_day']
    for model,cell in report['models'].items():
        nets=[];hits=bets=0;daily=defaultdict(Decimal);monthly=defaultdict(Decimal);loss=brier=0.
        for r in rows:
            f=r['forecasts'][model];p=f['p'];assert abs(sum(p)-1)<1e-9 and min(p)>0
            leaders=[i for i,v in enumerate(p) if max(p)-v<=1e-12];pick=leaders[0] if len(leaders)==1 else None
            net=Decimal(0) if pick is None else Decimal(r['prices'][pick])-1 if pick==r['y'] else Decimal(-1)
            assert f['pick']==pick and f['hit']==(pick==r['y']) and Decimal(f['net'])==net
            nets.append(net);hits+=f['hit'];bets+=pick is not None;daily[r['day']]+=net;monthly[r['day'][:7]]+=net
            loss-=math.log(p[r['y']]);brier+=sum((v-(i==r['y']))**2 for i,v in enumerate(p))
        total=sum(nets,Decimal(0));balance=peak=dd=Decimal(0)
        for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
        assert cell['games']==len(rows) and cell['bets']==bets and cell['hits']==hits
        assert Decimal(cell['net_units'])==total and abs(cell['roi']-float(total/bets))<1e-14
        assert cell['accuracy']==hits/bets and Decimal(cell['daily_close_drawdown_units'])==dd
        assert cell['monthly_net']=={m:str(n) for m,n in sorted(monthly.items())}
        assert abs(cell['logloss']-loss/len(rows))<1e-12 and abs(cell['brier']-brier/len(rows))<1e-12
    return dict(verified_forecasts=len(rows)*2,max_probability_replay_error=max(max_error,market_error),
                independent_probability_replay_by_model={'time_weighted_dc':max_error,'market':market_error},
                decimal_settlement='passed',official_price_receipts_certified=False)


if __name__=='__main__':
    result=verify();(HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result))
