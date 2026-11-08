"""Recompute draw-inclusive unit returns without importing the research model."""
import argparse
import gzip
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path


def calculate(rows, model):
    hits=[];nets=[];loss=[]
    for row in rows:
        price=row['price']
        h,a=Decimal(str(price['home'])),Decimal(str(price['away']))
        if model=='market':p=float((1/h)/(1/h+1/a))
        else:p=row[model+'_p']
        assert p is not None and math.isfinite(p) and 0<=p<=1
        pick='home' if p>=.5 else 'away'
        winner='home' if row['home_runs']>row['away_runs'] else 'away' if row['home_runs']<row['away_runs'] else 'draw'
        assert winner==price['winner']
        assert price['id'].endswith('full_game_2way')
        if winner=='draw':nets.append(Decimal(0));continue
        hit=pick==winner;hits.append(hit)
        nets.append(Decimal(str(price[pick]))-1 if hit else Decimal(-1))
        loss.append(-math.log(max(p if winner=='home' else 1-p,1e-12)))
    return {'n':len(rows),'nonvoid':len(hits),'hits':sum(hits),'accuracy':sum(hits)/len(hits),
            'roi':float(sum(nets)/len(rows)),'logloss':sum(loss)/len(loss)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    file=args.experiment/'predictions.jsonl.gz'
    rows=[json.loads(line) for line in gzip.open(file,'rt',encoding='utf8')]
    assert len({r['id'] for r in rows})==len(rows)
    summary=json.loads((args.experiment/'summary.json').read_text(encoding='utf8'))
    priced=[r for r in rows if r['price']]
    common=[r for r in priced if r['existing_p'] is not None]
    missing=[r for r in priced if r['existing_p'] is None]
    result={'audit':'passed','prediction_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
            'eval_n':len(rows),'priced_n':len(priced),'common_n':len(common),'missing_existing_n':len(missing),
            'metrics':{}}
    for label,subset,models in [('common',common,['market','existing','additive','pair']),
                                 ('all_priced',priced,['market','additive','pair']),
                                 ('missing_existing',missing,['market','additive','pair'])]:
        result['metrics'][label]={}
        for model in models:
            computed=calculate(subset,model);result['metrics'][label][model]=computed
            if label=='common':recorded=summary['metrics'][model]
            elif label=='all_priced':recorded=summary['oracle_priced_only_metrics'][model]
            else:continue
            assert computed['n']==recorded['n'] and computed['nonvoid']==recorded['nonvoid']
            for key,source_key in [('accuracy','hit_rate'),('roi','roi'),('logloss','log_loss')]:
                assert abs(computed[key]-recorded[source_key])<1e-12
    result['missing_existing_date_range']=[min(r['date'] for r in missing),max(r['date'] for r in missing)]
    result['common_date_range']=[min(r['date'] for r in common),max(r['date'] for r in common)]
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='metrics'}))


if __name__=='__main__':main()
