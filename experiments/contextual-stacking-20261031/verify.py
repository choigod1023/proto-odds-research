"""Independent settlement/aggregation checks on saved forecasts and original prices."""
from collections import defaultdict
from decimal import Decimal
import json
import math
from common import HERE,read_gz,save,sha


def verify():
    rows=read_gz(HERE/'settlements.jsonl.gz')
    features={r['id']:r for r in read_gz(HERE/'inputs/features.jsonl.gz')}
    labels={r['id']:r for r in read_gz(HERE/'inputs/labels.jsonl.gz')}
    forecasts={(r['id'],r['method']):r for r in read_gz(HERE/'forecasts.jsonl.gz')}
    result=json.loads((HERE/'results.json').read_text(encoding='utf8'))
    groups=defaultdict(list);cells=0
    if len(rows)!=1220 or len(forecasts)!=1220:raise AssertionError('unexpected case-method count')
    for row in rows:
        feature=features[row['id']];forecast=forecasts[(row['id'],row['method'])]
        probability=forecast['probabilities'];chosen=max(range(len(probability)),key=probability.__getitem__)
        outcome=labels[row['id']]['outcome'];hit=chosen==outcome
        payout=Decimal(str(feature['odds'][chosen])) if hit else Decimal(0)
        if row['pick']!=chosen or row['outcome']!=outcome or row['hit']!=int(hit):raise AssertionError('selection/label mismatch')
        if payout-1!=Decimal(row['profit']) or row['odds']!=feature['odds'][chosen]:raise AssertionError('price/payout mismatch')
        loss=-math.log(max(float(probability[outcome]),1e-15))
        if abs(loss-row['logloss'])>1e-12:raise AssertionError('logloss mismatch')
        market=feature['components']['market'];top=max(market)
        group='underdog' if market[chosen]<top-1e-12 else 'favorite' if sum(abs(p-top)<1e-12 for p in market)==1 else 'top_tie'
        if row['group']!=group:raise AssertionError('group mismatch')
        groups[(row['method'],None,None)].append(row)
        groups[(row['method'],row['league'],None)].append(row)
        groups[(row['method'],None,group)].append(row)
    for (method,league,group),values in groups.items():
        if league is not None:reported=result['by_league'][league][method]
        elif group is not None:reported=result['underdog'][method][group]
        else:reported=result['summary'][method]
        n=len(values);hits=sum(int(r['pick']==labels[r['id']]['outcome']) for r in values)
        roi=sum((Decimal(str(features[r['id']]['odds'][r['pick']])) if r['pick']==labels[r['id']]['outcome'] else Decimal(0))-1 for r in values)/n
        if reported['n']!=n or reported['hits']!=hits or abs(reported['hit_rate']-hits/n)>1e-12 or abs(reported['roi']-float(roi))>1e-12:
            raise AssertionError('aggregate mismatch')
        cells+=1
    unanimous_wrong=0
    for identifier in {r['id'] for r in rows}:
        f=features[identifier];vectors=list(f['components'].values());picks=[max(range(len(p)),key=p.__getitem__) for p in vectors]
        unique=all(sum(abs(q-max(p))<1e-12 for q in p)==1 for p in vectors)
        unanimous_wrong+=int(unique and len(set(picks))==1 and picks[0]!=labels[identifier]['outcome'])
    if unanimous_wrong!=result['shared_errors']['same_unique_wrong']:raise AssertionError('shared error mismatch')
    output={'saved_forecasts':len(forecasts),'independent_decimal_settlements_checked':len(rows),
            'grouped_metric_cells_checked':cells,'unanimous_unique_wrong':unanimous_wrong,
            'mismatches':0,'forecasts_sha256':sha(HERE/'forecasts.jsonl.gz'),
            'settlements_sha256':sha(HERE/'settlements.jsonl.gz'),
            'warning':'Arithmetic verification only; retrospective data, prices and receipt remain uncertified'}
    save(HERE/'verification.json',output);print(json.dumps(output))
    return output


if __name__=='__main__':verify()
