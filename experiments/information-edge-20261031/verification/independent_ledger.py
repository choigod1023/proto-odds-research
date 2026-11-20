"""Independent single-stake ledger for final frozen forecasts; no model imports."""
import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path


def read_jsonl(path):
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def audit(targets, forecasts, settlements=None):
    unique={}
    for t in targets:
        key=t['event_id']
        if key in unique: raise ValueError('duplicate target ID')
        unique[key]=t
    seen=set(); totals={}; details=[]
    for f in forecasts:
        key=(f['event_id'],f['mode'],f['model'])
        if key in seen: raise ValueError('duplicate forecast')
        seen.add(key); t=unique[f['event_id']]
        if t.get('actual_votes') is not True: raise ValueError('proxy used as actual')
        if f['league']!=t['league'] or f['decision_at']!=t['decision_at']: raise ValueError('identity/decision mismatch')
        sides=t['sides']; odds=t['odds']; y=t['outcome']; pick=f['pick']
        if type(y) is not int or type(pick) is not int or not 0<=y<len(sides) or not 0<=pick<len(sides) or len(odds)!=len(sides): raise ValueError('invalid outcome or pick')
        if any(Decimal(str(x))<=1 for x in odds): raise ValueError('invalid price')
        probabilities=f.get('probabilities')
        if probabilities is not None:
            if len(probabilities)!=len(sides): raise ValueError('invalid vector')
            vector=[Decimal(str(x)) for x in probabilities]
            if any(not x.is_finite() or x<0 or x>1 for x in vector): raise ValueError('invalid vector')
            # fixed91 saved rounded components need not sum exactly to one.
            if f['model'] not in ('fixed91_recomputed_rounded4',) and abs(sum(vector)-1)>Decimal('0.00000001'): raise ValueError('probability sum')
            if max(range(len(vector)),key=vector.__getitem__)!=pick: raise ValueError('pick differs from probability argmax')
        odds_chosen=Decimal(str(odds[pick])); hit=pick==y; net=odds_chosen-1 if hit else Decimal(-1)
        grouping=(f['mode'],f['model'],f['league']); count,wins,cash=totals.get(grouping,(0,0,Decimal(0)));totals[grouping]=(count+1,wins+hit,cash+net)
        details.append({'key':list(key),'outcome':y,'hit':hit,'profit':str(net),'price':str(odds_chosen)})
    if settlements is not None:
        other={}
        for x in settlements:
            k=(x['event_id'],x['mode'],x['model'])
            if k in other:raise ValueError('duplicate settlement')
            other[k]=x
        if set(other)!=seen:raise ValueError('settlement keys differ')
        for d in details:
            s=other[tuple(d['key'])]
            if Decimal(str(s['profit']))!=Decimal(d['profit']) or s['outcome']!=d['outcome']:raise ValueError('settlement mismatch')
    return {'records':len(details),'summaries':[{'mode':k[0],'model':k[1],'league':k[2],'bets':v[0],'wins':v[1],'net':str(v[2]),'accuracy':v[1]/v[0],'roi':float(v[2]/v[0])} for k,v in sorted(totals.items())], 'notes':['conditional on supplied forecast rows; not overall opportunity coverage','actual receipt, executed price, raw source identity and model provenance not verified']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--targets',type=Path,required=True);p.add_argument('--forecasts',type=Path,required=True);p.add_argument('--settlements',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inputs=[a.targets,a.forecasts]+([a.settlements] if a.settlements else [])
    report=audit(read_jsonl(a.targets),read_jsonl(a.forecasts),read_jsonl(a.settlements) if a.settlements else None)
    report['sha256']={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs}
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('audited records:',report['records'])
if __name__=='__main__': main()
