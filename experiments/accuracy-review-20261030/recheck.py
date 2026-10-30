import collections, gzip, hashlib, json, math
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent

def read(name):
    with gzip.open(P/'inputs'/name,'rt',encoding='utf-8') as f:
        return [json.loads(x) for x in f]

def main():
    targets=read('targets.jsonl.gz'); predictions=read('predictions.jsonl.gz')
    results=json.loads((P/'inputs/results.json').read_text(encoding='utf-8'))
    key=lambda r:(r['event_id'],r['cohort'])
    lookup={key(r):r for r in targets}
    assert len(lookup)==len(targets)
    agg=collections.defaultdict(lambda:collections.defaultdict(lambda:[0,0,0,Decimal(0)]))
    errors=[]; count=0
    for r in predictions:
        t=lookup[key(r)]
        for k in ('league','home','away','kickoff','decision_at','sides','odds','outcome','actual_votes'):
            if r[k]!=t[k]: errors.append([key(r),k])
        group=(r['league'],r['model'],r['mode'],r['actual_votes'])
        for name in ('candidate','market','fixed9','vote','home_constant'):
            a=agg[group][name]; a[0]+=1; x=r[name]
            if x is None: continue
            a[1]+=1
            pick=x['pick']; won=pick==t['outcome']; a[2]+=won
            profit=Decimal(str(t['odds'][pick]))-1 if won else Decimal(-1)
            a[3]+=profit
            if x['hit']!=int(won) or Decimal(x['profit_cents'])!=profit*100: errors.append([key(r),name,'settlement'])
            if name in ('candidate','market') and pick!=max(range(len(x['p'])), key=x['p'].__getitem__): errors.append([key(r),name,'pick'])
            if name=='market' and x['p']!=t['market_p']: errors.append([key(r),name,'probability'])
            count+=1
    checks=0; maxdiff=0.0
    for s in results['actual_vote_2026']:
        group=(s['league'],s['model'],s['mode'],True)
        for name,expected in s['metrics'].items():
            opportunities,bets,wins,net=agg[group][name]
            values={'opportunities':opportunities,'bets':bets,'nonvoid':bets,'wins':wins,'coverage':bets/opportunities if opportunities else None,'accuracy':wins/bets if bets else None,'roi':float(net/bets) if bets else None}
            for k,v in values.items():
                q=expected[k]
                if v is None or q is None:
                    if v != q: errors.append([group,name,k,v,q])
                elif isinstance(v,(int,float)):
                    d=abs(v-q); maxdiff=max(maxdiff,d)
                    if d>1e-12: errors.append([group,name,k,v,q])
                checks+=1
    metrics={}
    for league,model in results['selected'].items():
        if model is None: continue
        metrics[league]={}
        for mode in ('frozen','prequential'):
            a=agg[(league,model,mode,True)]['candidate']; n,b,w,net=a
            metrics[league][mode]={'opportunities':n,'bets':b,'wins':w,'coverage':b/n,'accuracy':w/b,'roi':float(net/b)}
    result={'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (P/'inputs').iterdir()},'target_rows':len(targets),'prediction_rows':len(predictions),'independent_settlements':count,'summary_values_checked':checks,'max_summary_absolute_difference':maxdiff,'errors':errors,'selected_actual_vote_metrics':metrics}
    (P/'independent-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if errors: raise SystemExit(1)
if __name__=='__main__': main()
