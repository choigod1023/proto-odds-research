"""Other-period market-only check; never substitutes for unavailable tipster history."""
from collections import Counter,defaultdict
from pathlib import Path
from datetime import date,timedelta
import argparse
import csv
import hashlib
import json
import re
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from prepare_baseball_feature_lab import canonical_map
from backtest_baseball_feature_partitions import measure


def absolute_interval(rows,ps,mask):
    groups=defaultdict(lambda:np.zeros(4))
    for r,p,keep in zip(rows,ps,mask):
        if not keep:continue
        d=date.fromisoformat(r['date']);week=(d-timedelta(days=d.weekday())).isoformat();draw=r['hg']==r['ag']
        correct=(p>=.5)==(r['hg']>r['ag']);price=r['historical_prices'][int(p<.5)]
        net=0 if draw else price-1 if correct else -1
        groups[week]+=[int(correct and not draw),net,int(not draw),1]
    if len(groups)<2:return dict(weeks=len(groups))
    a=np.array(list(groups.values()));rng=np.random.default_rng(20261029)
    sums=a[rng.integers(len(a),size=(3000,len(a)))].sum(axis=1);valid=sums[:,2]>0
    return dict(weeks=len(groups),accuracy95=np.quantile(sums[valid,0]/sums[valid,2],[.025,.975]).tolist(),
        roi95=np.quantile(sums[:,1]/sums[:,3],[.025,.975]).tolist())


def load_prices(archive,team_map,history):
    clean=lambda s:re.sub(r'\s+','',str(s)).casefold()
    aliases={l:{clean(k):v for k,v in team_map[l].items()} for l in ['KBO','NPB']}
    def canonical(league,name):
        if league=='MLB':return canonical_map(team_map)(name)
        return aliases[league].get(clean(name),name if name in aliases[league].values() else None)
    known={(r['league'],r['date'],r['home'],r['away']):(r['hg'],r['ag']) for r in history if r['date'][:4] in ['2023','2024']}
    groups=defaultdict(set);excluded=Counter()
    with archive.open(encoding='utf8') as f:
        for r in csv.DictReader(f):
            if r['league'] not in ['KBO','NPB','MLB'] or r['market_family']!='승패' or r['n_way']!='2' or r['is_void'].lower()=='true':continue
            h=re.fullmatch(r'(.+?)\s+(\d+)',r['home'].strip());a=re.fullmatch(r'(\d+)\s+(.+)',r['away'].strip())
            dt=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',r['date_text'])
            if not h or not a or not dt:continue
            year=int(r['year'])-int(r['round']=='1' and dt[1]=='12');day=f'{year}-{dt[1]}-{dt[2]}'
            home,away=canonical(r['league'],h[1]),canonical(r['league'],a[2]);score=(int(h[2]),int(a[1]))
            k=(r['league'],day,home,away)
            if known.get(k)!=score:excluded['not_in_curated_history_or_score_disagrees']+=1;continue
            prices=tuple(float(x) for x in r['odds'].split(','))
            if len(prices)!=2 or min(prices)<=1:continue
            groups[k].add((prices,score))
    rows=[]
    for k,values in groups.items():
        if len(values)!=1:excluded['multiple_price_versions']+=1;continue
        prices,score=next(iter(values));rows.append(dict(league=k[0],date=k[1],home=k[2],away=k[3],historical_prices=list(prices),hg=score[0],ag=score[1]))
    return sorted(rows,key=lambda r:(r['league'],r['date'],r['home'],r['away'])),dict(excluded)


def run(archive,team_map,history,folder):
    read=lambda f:[json.loads(s) for s in f.read_text(encoding='utf8').splitlines() if s]
    rows,excluded=load_prices(archive,json.loads(team_map.read_text(encoding='utf8')),read(history))
    result={}
    for league in ['KBO','NPB','MLB']:
        for year in ['2023','2024']:
            batch=[r for r in rows if r['league']==league and r['date'].startswith(year)]
            probs=np.array([market_probabilities(r['historical_prices'])[0] for r in batch]);confidence=np.maximum(probs,1-probs)
            parts={'all':np.ones(len(batch),dtype=bool),'confidence60_65':(confidence>=.6)&(confidence<.65),'confidence60plus':confidence>=.6}
            result[league+':'+year]={n:dict(metrics=measure(batch,probs,mask),interval=absolute_interval(batch,probs,mask)) for n,mask in parts.items()}
    current=read(folder/'baseball-common-cohort.jsonl');current_result={}
    for league in ['KBO','NPB','MLB']:
        batch=[];ps=[]
        for r in current:
            if r['league']!=league:continue
            odds=[r['prices'][r['order'].index(s)] for s in ['home','away']]
            batch.append(dict(r,historical_prices=odds));m=market_probabilities(odds)[0];ps.append(.9*m+.1*r['vote_counts'].get('home',0)/r['voters'])
        market=np.array([market_probabilities(r['historical_prices'])[0] for r in batch]);confidence=np.maximum(market,1-market)
        parts={'confidence60_65':(confidence>=.6)&(confidence<.65),'confidence60plus':confidence>=.6}
        current_result[league]={n:dict(metrics=measure(batch,np.array(ps),mask),matched_market=measure(batch,market,mask),
            interval=absolute_interval(batch,np.array(ps),mask),by_half={label:measure(batch,np.array(ps),mask&np.array([r['date']<'2026-07-01' if label=='before_july' else r['date']>='2026-07-01' for r in batch])) for label in ['before_july','july_onward']}) for n,mask in parts.items()}
    report=dict(other_period_scope='market only, no 2023/24 tipster observations; retrospective hypothesis follow-up',
        other_period=result,current_cohort=current_result,excluded=excluded,total_other_period_games=len(rows),
        archive_sha256=hashlib.sha256(archive.read_bytes().replace(b'\r\n',b'\n')).hexdigest())
    (folder/'other-period-partitions.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (folder/'other-period-price-facts.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8',newline='\n')
    print(json.dumps(dict(total_other_period_games=len(rows),results={k:{n:dict(n=v['metrics']['n'],accuracy=v['metrics']['accuracy'],roi=v['metrics']['roi']) for n,v in parts.items()} for k,parts in result.items()},current={l:{n:v['metrics'] for n,v in parts.items()} for l,parts in current_result.items()}),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['archive','team-map','history','folder']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.archive,a.team_map,a.history,a.folder)
