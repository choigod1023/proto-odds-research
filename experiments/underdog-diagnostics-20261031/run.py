"""Descriptive diagnostics of fixed, already inspected prediction records."""
import collections
from decimal import Decimal
import gzip
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read_gz(path):
    with gzip.open(path, 'rt', encoding='utf8') as f:
        return [json.loads(line) for line in f]

def read_jsonl(path):
    with path.open(encoding='utf8') as f:
        return [json.loads(line) for line in f]

def classify(market, pick):
    if not 0 <= pick < len(market) or len(market) not in (2, 3):
        raise ValueError('bad pick or market')
    if any(not math.isfinite(x) or x <= 0 or x >= 1 for x in market) or abs(sum(market)-1) > 1e-6:
        raise ValueError('incomplete market')
    maximum=max(market)
    if market[pick] < maximum:
        return 'underdog'
    return 'favorite' if sum(x == maximum for x in market) == 1 else 'top_tie'

def price_profit(odds, pick, outcome):
    price=Decimal(str(odds[pick]))
    if not price.is_finite() or price <= 1:
        raise ValueError('invalid odds')
    return price-1 if pick == outcome else Decimal(-1)

def wilson(hit, n):
    if not n: return None
    z=1.959963984540054; p=hit/n; den=1+z*z/n
    center=(p+z*z/(2*n))/den
    width=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return [max(0,center-width),min(1,center+width)]

def instant(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None: raise ValueError('naive decision time')
    return d.astimezone(timezone.utc)

def paired_interval(pairs):
    weeks=collections.defaultdict(list)
    for a,b in pairs:
        d=instant(a['decision_at']); iso=d.isocalendar(); weeks[(iso.year,iso.week)].append((a,b))
    if len(weeks)<2: return None
    blocks=list(weeks.values()); rng=random.Random(20261031); acc=[]; roi=[]
    for _ in range(2000):
        sample=[pair for _ in blocks for pair in rng.choice(blocks)]
        acc.append(sum(a['hit']-b['hit'] for a,b in sample)/len(sample))
        roi.append(float(sum(Decimal(a['profit'])-Decimal(b['profit']) for a,b in sample)/len(sample)))
    acc.sort(); roi.sort()
    return {'weeks':len(weeks),'iterations':2000,'accuracy_delta_95':[acc[49],acc[1949]],'roi_delta_95':[roi[49],roi[1949]]}

def summary(rows):
    n=len(rows); hits=sum(r['hit'] for r in rows)
    if not n: return {'n':0,'hits':0}
    ps=[r['predicted_p'] for r in rows if r['predicted_p'] is not None]
    run=[]; longest=[]
    for r in sorted(rows,key=lambda x:(instant(x['decision_at']),x['event_id'])):
        if r['hit']: run=[]
        else:
            run=run+[r]
            if len(run)>len(longest): longest=run[:]
    return {'n':n,'hits':hits,'accuracy':hits/n,'wilson95':wilson(hits,n),
            'mean_market_p':sum(r['market_p'] for r in rows)/n,
            'mean_model_p':sum(ps)/len(ps) if len(ps)==n else None,
            'mean_odds':sum(r['odds'] for r in rows)/n,
            'profit_units':float(sum((Decimal(r['profit']) for r in rows), Decimal(0))),
            'roi':float(sum((Decimal(r['profit']) for r in rows),Decimal(0))/n),
            'longest_loss_run':len(longest),
            'run_start':longest[0]['decision_at'] if longest else None,
            'run_end':longest[-1]['decision_at'] if longest else None,
            'run_independent_market_failure_product':math.prod(1-r['market_p'] for r in longest) if longest else None}

def emit(event, model, pick, probs):
    if pick is None: return None
    market=event['market_p']; sides=event['sides']; odds=event['odds']; outcome=event['outcome']
    if len(sides)!=len(market) or len(odds)!=len(market) or not (0<=outcome<len(market)):
        raise ValueError('incomplete event')
    for o in odds:
        if not Decimal(str(o)).is_finite() or Decimal(str(o))<=1: raise ValueError('invalid odds vector')
    kind=classify(market,pick)
    if probs is not None and (len(probs)!=len(market) or any(not math.isfinite(p) or p<0 or p>1 for p in probs) or abs(sum(probs)-1)>0.0003):
        raise ValueError('invalid model probability')
    return {'event_id':event['event_id'],'league':event['league'],'decision_at':event['decision_at'], 'model':model,
      'group':kind,'market_rank':1+sum(p>market[pick] for p in market),'side':sides[pick],
      'market_type':str(len(market))+'-way','pick':pick,'outcome':outcome,'hit':int(pick==outcome),
      'market_p':market[pick],'predicted_p':probs[pick] if probs is not None else None,'odds':odds[pick],
      'profit':str(price_profit(odds,pick,outcome))}

def collect():
    inputs=ROOT/'inputs'; protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf8'))
    for name,expected in protocol['files_sha256'].items():
        if hashlib.sha256((inputs/name).read_bytes()).hexdigest()!=expected: raise ValueError('input hash mismatch')
    targets=read_gz(inputs/'targets.jsonl.gz'); target={r['event_id']:r for r in targets}
    if len(target)!=len(targets): raise ValueError('duplicate target id')
    forecast=read_jsonl(inputs/'expert-forecasts.jsonl'); settled=read_jsonl(inputs/'expert-settlements.jsonl')
    settlement={(r['event_id'],r['mode'],r['model']):r for r in settled}
    if len(settlement)!=len(settled): raise ValueError('duplicate settlement')
    wanted={('frozen','selected'):'expert_frozen',('prequential','selected'):'expert_prequential',('frozen','market'):'market',('comparison','fixed91_recomputed'):'fixed91',('comparison','author_plurality'):'vote'}
    out={'expert_122':[],'state_445':[]}
    for f in forecast:
        key=(f['mode'],f['model'])
        if key not in wanted: continue
        t=target[f['event_id']]
        if f['league']!=t['league'] or f['decision_at']!=t['decision_at'] or not t['actual_votes']: raise ValueError('target mismatch')
        if key==('frozen','market') and f['probabilities']!=t['market_p']: raise ValueError('expert market mismatch')
        row=emit(t,wanted[key],f['pick'], f['probabilities'] if key!=('comparison','author_plurality') else None)
        s=settlement[(f['event_id'],*key)]
        if s['outcome']!=t['outcome'] or s['pick']!=f['pick'] or (row is not None and Decimal(row['profit'])!=Decimal(s['profit'])): raise ValueError('expert settlement mismatch')
        if row is not None: out['expert_122'].append(row)
    if len({r['event_id'] for r in out['expert_122']})!=122: raise ValueError('expert cohort mismatch')
    raw=read_gz(inputs/'state-predictions.jsonl.gz')
    for f in raw:
        if not f['actual_votes'] or f['model']!='outcome-static' or f['mode'] not in ('frozen','prequential'): continue
        t=target[f['event_id']]
        if any(f[k]!=t[k] for k in ('event_id','league','decision_at','sides','odds','outcome')): raise ValueError('state target mismatch')
        if f['market']['p']!=t['market_p']: raise ValueError('market probability mismatch')
        models=([('market','market'),('fixed91','fixed9'),('vote','vote')] if f['mode']=='frozen' else [])
        if f['league']!='K리그2': models.append(('state_'+f['mode'],'candidate'))
        for name,src in models:
            v=f[src]; row=emit(t,name,v['pick'],None if name=='vote' else v['p'])
            if row is None or row['hit']!=v['hit'] or Decimal(row['profit'])*100!=Decimal(v['profit_cents']): raise ValueError('state settlement mismatch')
            out['state_445'].append(row)
    if len({r['event_id'] for r in out['state_445']})!=445: raise ValueError('state cohort mismatch')
    for cohort,rows in out.items():
        seen=set()
        for r in rows:
            k=(r['event_id'],r['model'])
            if k in seen: raise ValueError('duplicate model/event')
            seen.add(k)
    return out

def analyze(rows):
    groups=collections.defaultdict(list)
    for r in rows:
        for key in [('overall',r['model'],r['group']),('league',r['model'],r['group'],r['league']),('market_type',r['model'],r['group'],r['market_type']),('rank',r['model'],r['group'],r['market_type'],str(r['market_rank'])),('side',r['model'],r['group'],r['market_type'],r['side'])]: groups[key].append(r)
    metrics=[{'dimensions':list(k),**summary(v)} for k,v in sorted(groups.items())]
    market={r['event_id']:r for r in rows if r['model']=='market'}; flips={}
    for model in sorted({r['model'] for r in rows}-{'market'}):
        changed=[(r,market[r['event_id']]) for r in rows if r['model']==model and r['event_id'] in market and r['pick']!=market[r['event_id']]['pick']]
        pairs=[(r,market[r['event_id']]) for r in rows if r['model']==model and r['event_id'] in market]
        flips[model]={'paired_n':len(pairs),'paired_interval':paired_interval(pairs),'changed_interval':paired_interval(changed),'n':len(changed),'model_hits':sum(a['hit'] for a,b in changed),'market_hits':sum(b['hit'] for a,b in changed),'model_profit':str(sum((Decimal(a['profit']) for a,b in changed),Decimal(0))),'market_profit':str(sum((Decimal(b['profit']) for a,b in changed),Decimal(0)))}
    return {'groups':metrics,'selection_changes':flips}

def main():
    data=collect(); result={k:analyze(v) for k,v in data.items()}
    (ROOT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    with (ROOT/'ledger.jsonl').open('w',encoding='utf8',newline='\n') as f:
        for cohort,records in data.items():
            for r in records: f.write(json.dumps({'cohort':cohort,**r},ensure_ascii=False,allow_nan=False)+'\n')
    print('validated rows', {k:len(v) for k,v in data.items()})

if __name__=='__main__': main()
