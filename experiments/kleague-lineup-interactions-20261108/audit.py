"""Independent arithmetic, paired coverage and lineup availability audit."""
import gzip
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    connected={r['id']:r for r in map(json.loads,gzip.open(HERE/'connected.jsonl.gz','rt',encoding='utf8'))}
    assert len(connected)==789
    for r in connected.values():
        for side in ['home','away']:
            assert len(r[side+'_xi'])==len(set(r[side+'_xi']))==11
            prior=r[side+'_prior']
            if prior:
                assert date.fromisoformat(prior['day'])+timedelta(days=4)<=date.fromisoformat(r['day'])
    groups=defaultdict(list)
    seen=set()
    for line in gzip.open(HERE/'predictions.jsonl.gz','rt',encoding='utf8'):
        r=json.loads(line);source=connected[r['event_id']]
        assert r['day']==source['day'] and r['odds']==source['odds'] and r['y']==source['y']
        assert source['home_projected'] and source['away_projected']
        assert r['day']>='2026-01-01'
        y=0 if source['home_score']>source['away_score'] else 2 if source['home_score']<source['away_score'] else 1
        assert y==r['y']
        p=r['p'];assert len(p)==3 and all(math.isfinite(x) and x>=0 for x in p)
        assert abs(sum(p)-1)<1e-10
        pick=max(range(3),key=p.__getitem__)
        assert pick==r['pick'] and r['hit']==(pick==y)
        net=Decimal(str(r['odds'][pick]))-1 if pick==y else Decimal(-1)
        assert net==Decimal(r['net'])
        key=(r['model'],r['event_id']);assert key not in seen;seen.add(key)
        groups[r['model']].append(r)
    summary=json.loads((HERE/'summary.json').read_text(encoding='utf8'))
    ids={r['event_id'] for r in groups['market']}
    recomputed={}
    for model,rows in groups.items():
        assert ids=={r['event_id'] for r in rows}
        n=len(rows);acc=sum(r['hit'] for r in rows)/n
        roi=float(sum((Decimal(r['net']) for r in rows),Decimal(0))/n)
        ll=-sum(math.log(r['p'][r['y']]) for r in rows)/n
        recorded=summary['models'][model]
        assert recorded['n']==n and abs(acc-recorded['accuracy'])<1e-12
        assert abs(roi-recorded['roi'])<1e-12 and abs(ll-recorded['logloss'])<1e-12
        recomputed[model]={'n':n,'accuracy':acc,'roi':roi,'logloss':ll}
    output={'audit':'passed','n_connected':len(connected),'n_common_eval':len(ids),
            'n_model_prediction_rows':len(seen),'metrics':recomputed,
            'strict_pregame_capture_count':0,'not_operational_model_replay':True}
    (HERE/'audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in output.items() if k!='metrics'}))


if __name__=='__main__':main()
