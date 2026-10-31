"""Verify the worker ledgers from source and integrate without model selection."""
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
import gzip,hashlib,json
from pathlib import Path

HERE=Path(__file__).resolve().parent
MODELS=['market','outcome_form','market_residual','half_market_form']
POLICIES=['all','favorites','underdogs','market_same_underdogs']


def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(line) for line in f]


def check_cell(rows,model,policy,v):
    selected=[]
    for r in rows:
        group=r['predictions'][model]['group']
        if policy=='all' or policy=='favorites' and group=='favorite' or policy in ('underdogs','market_same_underdogs') and group=='underdog':selected.append(r)
    label='market' if policy=='market_same_underdogs' else model
    profits=[Decimal(r['predictions'][label]['net']) for r in selected];total=sum(profits,Decimal(0))
    n=len(selected);hits=sum(r['predictions'][label]['hit'] for r in selected)
    assert (v['opportunities'],v['bets'],v['hits'])==(len(rows),n,hits)
    assert Decimal(v['net_units'])==total
    assert v['roi']==(float(total/n) if n else None)
    assert v['accuracy']==(hits/n if n else None)
    assert v['net_per_opportunity']==(float(total/len(rows)) if rows else None)
    monthly={r['day'][:7]:Decimal(0) for r in rows};daily={r['day']:Decimal(0) for r in rows}
    for r,p in zip(selected,profits):daily[r['day']]+=p;monthly[r['day'][:7]]+=p
    assert v['monthly_net']=={k:float(n) for k,n in sorted(monthly.items())}
    values=[Decimal(0)]
    for k in sorted(daily):values.append(values[-1]+daily[k])
    peak=Decimal(0);dd=Decimal(0)
    for x in values:peak=max(peak,x);dd=max(dd,peak-x)
    assert v['drawdown_units']==float(dd)
    assert v['positive_months']==sum(n>0 for n in monthly.values())
    assert v['negative_months']==sum(n<0 for n in monthly.values())


def verify():
    source={r['id']:r for p in sorted((HERE/'inputs').glob('*.jsonl.gz')) for r in read(p)}
    forecasts=0;cells=0;events=0;inputs=set();reports={};allrows=defaultdict(list)
    for path in sorted((HERE/'results').glob('*/*/ledger.jsonl.gz')):
        rows=read(path);folder=path.parent;report=json.loads((folder/'results.json').read_text(encoding='utf8'))
        assert len({r['id'] for r in rows})==len(rows)
        assert report['count']==len(rows) and report['fallback_events']==sum(r['fallback'] for r in rows)
        state={r['month']:r for r in json.loads((folder/'states.json').read_text(encoding='utf8'))}
        for r in rows:
            t=source[r['id']]
            for f in ('odds','y','cohort','sport','league','day','kickoff','sources'):assert r[f]==t[f]
            q=[Decimal(1)/Decimal(str(v)) for v in t['odds']];q=[x/sum(q) for x in q]
            assert all(abs(float(v)-r['market_p'][i])<1e-12 for i,v in enumerate(q))
            month=r['day'][:7];s=state[month]
            assert r['month_state_sha256']==s['state_sha256'] and r['fallback']==s['fallback']
            assert s['training_cutoff_inclusive']<month+'-01'
            for name,v in r['predictions'].items():
                assert name in MODELS
                p=v['p'];assert len(p)==len(q) and all(x>=0 for x in p) and abs(sum(p)-1)<1e-10
                pick=max(range(len(p)),key=p.__getitem__);assert v['pick']==pick
                mx=max(q);g='underdog' if float(mx-q[pick])>1e-12 else ('top_tie' if sum(abs(float(x-mx))<=1e-12 for x in q)>1 else 'favorite')
                assert g==v['group']
                expected=Decimal(str(t['odds'][pick]))-1 if pick==t['y'] else Decimal(-1)
                assert Decimal(v['net'])==expected and v['hit']==(pick==t['y'])
                assert v['rank']==sum(float(x-q[pick])>1e-12 for x in q)+1
                assert v['draw']==(r['sport']=='sc' and pick==1)
                if r['fallback']:assert v['p']==r['predictions']['market']['p']
                forecasts+=1
            inputs.add((r['cohort'],r['id']));events+=1
        for month,s in state.items():
            expected=sum(r['day']<=s['training_cutoff_inclusive'] for r in rows)
            assert s['training_rows']==expected
            expected_hash=hashlib.sha256(json.dumps(s['states'],sort_keys=True).encode()).hexdigest()
            assert s['state_sha256']==expected_hash
            if not s['fallback']:
                assert all(v['training_rows']==expected for v in s['states'].values())
        analyses=[(rows,report['full'])]
        analyses.extend(([r for r in rows if r['day'][:4]==y],a) for y,a in report['by_year'].items())
        analyses.extend(([r for r in rows if r['league']==l],a) for l,a in report['by_league'].items())
        cut=report['chronological_split_day']
        analyses.extend([([r for r in rows if r['day']<cut],report['first_half']),([r for r in rows if r['day']>=cut],report['second_half'])])
        for subset,analysis in analyses:
            for name in MODELS:
                for policy in POLICIES:check_cell(subset,name,policy,analysis[name]['policies'][policy]);cells+=1
        reports[report['cohort']+'/'+report['sport']]=report
        allrows[report['cohort']].extend(rows)
    # Every supplied fixture appears in exactly its sport worker, not just a sample.
    assert len(inputs)==len(source)==events
    audit={'source_fixtures':len(source),'worker_ledger_events':events,'independently_verified_forecasts':forecasts,
           'independently_verified_policy_cells':cells,'mismatches':0,
           'domestic_sports':sorted({r['sport'] for r in allrows['domestic_archive']}),
           'independent_pregame_or_executable_price_certified':0,
           'source_cohorts_not_independent_or_summed':True,
           'worker_result_lf_sha256':{str(p.relative_to(HERE)).replace('\\','/'):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in sorted((HERE/'results').glob('*/*/results.json'))}}
    (HERE/'verification.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf8',newline='\n')
    return reports,allrows,audit


if __name__=='__main__':
    _,_,audit=verify();print(json.dumps(audit))
