"""Recalculate external-reference-price results; does not certify tradable products."""
import argparse
import gzip
import json
import math
from decimal import Decimal
from pathlib import Path


def evaluate(records, probability, outcome, odds):
    hits=0;net=Decimal(0);ll=0;seen=set()
    for r in records:
        assert r['id'] not in seen;seen.add(r['id'])
        p=probability(r);y=outcome(r);prices=odds(r)
        assert len(p)==len(prices) and all(math.isfinite(v) and v>=0 for v in p)
        assert abs(sum(p)-1)<1e-8 and 0<=y<len(p)
        choice=max(range(len(p)),key=p.__getitem__);hit=choice==y
        hits+=hit;net+=Decimal(str(prices[choice]))-1 if hit else Decimal(-1)
        ll-=math.log(max(p[y],1e-12))
    return {'n':len(records),'hits':hits,'accuracy':hits/len(records),
            'roi':float(net/len(records)),'logloss':ll/len(records)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--nba',type=Path,required=True)
    parser.add_argument('--soccer',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result={}
    nba=[json.loads(l) for l in gzip.open(args.nba/'priced-matches.jsonl.gz','rt',encoding='utf8')]
    summary=json.loads((args.nba/'priced-summary.json').read_text(encoding='utf8'))
    nba_metrics={}
    for name in ['market','team','player','pair']:
        def probability(r):
            h=r['market_home_probability'] if name=='market' else r['home_win_probability'][name]
            return [h,1-h]
        for r in nba:
            assert r['score'][0]!=r['score'][1]
            assert r['settled_winner']==(0 if r['score'][0]>r['score'][1] else 1)
        m=evaluate(nba,probability,lambda r:r['settled_winner'],lambda r:r['odds'])
        old=summary['metrics'][name]
        assert m['n']==old['n'] and m['hits']==old['hits']
        assert abs(m['roi']-old['roi'])<1e-12 and abs(m['logloss']-old['log_loss'])<1e-12
        nba_metrics[name]=m
    result['nba']={'audit':'passed','reference_price_games':len(nba),'strict_product_rule_proof':False,'models':nba_metrics}
    soccer=[json.loads(l) for l in gzip.open(args.soccer/'priced_predictions.jsonl.gz','rt',encoding='utf8')]
    summary=json.loads((args.soccer/'price_summary.json').read_text(encoding='utf8'))
    soccer_metrics={}
    for name in ['market','poisson','sequence','state_path']:
        for r in soccer:
            y=0 if r['home_score']>r['away_score'] else 2 if r['home_score']<r['away_score'] else 1
            assert r['result']==y
        m=evaluate(soccer,lambda r:r[name],lambda r:r['result'],lambda r:r['odds'])
        old=summary['metrics'][name]
        assert m['n']==old['n'] and m['hits']==old['hits']
        assert abs(m['roi']-old['roi'])<1e-12 and abs(m['logloss']-old['log_loss'])<1e-12
        soccer_metrics[name]=m
    result['soccer']={'audit':'passed','reference_price_games':len(soccer),
                      'price_source':'third-party Football-Data/Bet365 mirror, NOT Proto',
                      'execution_proven':False,'models':soccer_metrics}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result))


if __name__=='__main__':main()
