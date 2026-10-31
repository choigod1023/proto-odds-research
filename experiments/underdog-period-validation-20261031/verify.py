"""Independent raw-price settlement and aggregate verification (no runner import)."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCE=ROOT/'experiments/dynamic-score-state-model-20261030'


def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]


def verify():
    rows=read(HERE/'ledger.jsonl.gz')
    targets=read(SOURCE/'inputs/targets.jsonl.gz')
    source={(t['league'],t['event_id'],t['cohort']):t for t in targets}
    assert len(source)==len(targets)
    seen=set();checks=0
    saved={(r['league'],r['event_id'],r['cohort'],r['mode']):r for r in read(SOURCE/'results/predictions.jsonl.gz') if r['model']=='score-static'}
    for r in rows:
        t=source[(r['league'],r['event_id'],r['source_cohort'])]
        key=(r['id'],r['mode']);assert key not in seen;seen.add(key)
        assert r['odds']==t['odds'] and r['outcome']==t['outcome']
        assert r['decision_at']==t['decision_at'] and r['kickoff_at']==t['kickoff']
        dt=datetime.fromisoformat(t['decision_at']).astimezone(timezone.utc)
        assert (r['year'],r['day'],r['month'],r['week'])==(dt.year,dt.date().isoformat(),dt.strftime('%Y-%m'),dt.strftime('%G-W%V'))
        assert r['cohort']==('actual_saved_quote' if t['actual_votes'] else 'archive_price_proxy')
        assert r['price_evidence']==t['price_evidence']
        if r['year']==2026:
            old=saved[(r['league'],r['event_id'],r['source_cohort'],r['mode'])]
            assert r['probabilities']==old['candidate']['p']
            assert r['void']==old['candidate']['void']==old['market']['void']
        p=r['probabilities'];m=t['market_p']
        assert r['market_probabilities']==m
        sp=max(range(len(p)),key=p.__getitem__);mp=max(range(len(m)),key=m.__getitem__)
        assert (r['score_pick'],r['market_pick'])==(sp,mp)
        top=max(m)
        group='underdog' if top-m[sp]>1e-12 else ('top_tie' if sum(abs(v-top)<=1e-12 for v in m)>1 else 'favorite')
        assert group==r['group']
        for label,pick in [('score',sp),('market',mp)]:
            net=Decimal(0) if r['void'] else (Decimal(str(t['odds'][pick]))-1 if pick==t['outcome'] else Decimal(-1))
            assert Decimal(r[label+'_net'])==net
            assert r[label+'_hit']==int(pick==t['outcome'] and not r['void'])
            checks+=1
    result=json.loads((HERE/'results.json').read_text(encoding='utf8'))
    cells=0
    for key,periods in result.items():
        cohort,year,mode=key.split('/');year=int(year)
        universe=[r for r in rows if (r['cohort'],r['year'],r['mode'])==(cohort,year,mode)]
        if year==2026:
            a={r['id'] for r in universe if r['period']=='earlier'}
            b={r['id'] for r in universe if r['period']=='discovery'}
            assert not a&b and len(a|b)==len(universe)
        for period,analyses in periods.items():
            subset=[r for r in universe if period=='all' or r['period']==period]
            for league,analysis in [('ALL',analyses['all_leagues']),*analyses['by_league'].items()]:
                events=subset if league=='ALL' else [r for r in subset if r['league']==league]
                for policy,v in analysis['policies'].items():
                    bets=events
                    if policy=='score_favorites':bets=[r for r in events if r['group']=='favorite']
                    if policy in ('score_underdogs','market_same_underdog_events'):bets=[r for r in events if r['group']=='underdog']
                    label='market' if policy.startswith('market') else 'score'
                    net=sum((Decimal(r[label+'_net']) for r in bets),Decimal(0))
                    hits=sum(r[label+'_hit'] for r in bets);settled=sum(not r['void'] for r in bets)
                    assert (v['opportunities'],v['bets'],v['hits'],v['nonvoid_bets'])==(len(events),len(bets),hits,settled)
                    assert Decimal(v['net_units'])==net
                    for field,expected in [('coverage',len(bets)/len(events) if events else None),('turnover_roi',float(net/len(bets)) if bets else None),('net_per_opportunity',float(net/len(events)) if events else None),('hit_rate',hits/settled if settled else None)]:
                        assert v[field]==expected
                    months={r['month']:Decimal(0) for r in events};days={r['day']:Decimal(0) for r in events}
                    for r in bets:
                        months[r['month']]+=Decimal(r[label+'_net']);days[r['day']]+=Decimal(r[label+'_net'])
                    assert v['monthly_net_units']=={m:float(n) for m,n in sorted(months.items())}
                    cumulative=[Decimal(0)]
                    for day in sorted(days):cumulative.append(cumulative[-1]+days[day])
                    drawdown=max((max(cumulative[:i+1])-x for i,x in enumerate(cumulative)),default=Decimal(0))
                    assert v['max_daily_close_drawdown_units']==float(drawdown)
                    assert [v['negative_months'],v['positive_months'],v['zero_months']]==[sum(n<0 for n in months.values()),sum(n>0 for n in months.values()),sum(n==0 for n in months.values())]
                    cells+=1
                for under,name in [(False,'paired_all'),(True,'paired_underdogs')]:
                    e=[r for r in events if not under or r['group']=='underdog']
                    delta=sum((Decimal(r['score_net'])-Decimal(r['market_net']) for r in e),Decimal(0))
                    assert analysis[name]['n']==len(e)
                    expected=float(delta/len(e)) if e else None
                    actual=analysis[name]['delta_roi']
                    assert actual is None if expected is None else abs(actual-expected)<1e-12
    output={'ledger_rows':len(rows),'raw_price_settlements_checked':checks,'aggregate_policy_cells_checked':cells,'mismatches':0,'independent_verifier_imports_runner':False,
            'artifact_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/'results.json',HERE/'ledger.jsonl.gz',HERE/'audit.json')}}
    (HERE/'verification.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(output))
    return output


if __name__=='__main__':verify()
