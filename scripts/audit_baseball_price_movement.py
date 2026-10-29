"""Price movement from timestamped pregame rows only; final rows are forbidden."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse,csv,json,re,sys,math,hashlib
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from prepare_baseball_feature_lab import canonical_map
from backtest_baseball_feature_partitions import measure

KST=timezone(timedelta(hours=9))


def quote_fact(row,targets,canonical):
    if row.get('league') not in ['KBO','NPB','MLB'] or row.get('market_family')!='승패' or row.get('n_way')!='2':return None,'other_market'
    if row.get('result')!='경기전':return None,'not_pregame_source_row'
    h=re.sub(r'\s+\d+$','',row['home'].strip());a=re.sub(r'^\d+\s+','',row['away'].strip())
    home,away=canonical(row['league'],h),canonical(row['league'],a)
    dt=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',row['date_text'])
    if not dt:return None,'missing_quote_fixture_time'
    year=int(row['year'])-int(row['round']=='1' and dt[1]=='12')
    start=datetime(year,*map(int,dt.groups()),tzinfo=KST);key=(row['league'],start.date().isoformat(),home,away)
    if key not in targets:return None,'not_in_common_cohort'
    observed=datetime.fromisoformat(row['ts'].replace('Z','+00:00'))
    if observed.tzinfo is None:return None,'observation_without_zone'
    if observed>=start-timedelta(minutes=10):return None,'at_or_after_tminus10'
    odds=[float(v) for v in row['odds'].split(',')]
    if len(odds)!=2 or any(not math.isfinite(v) or v<=1 for v in odds):return None,'invalid_odds'
    return dict(key=key,observed_at=observed.isoformat(),start_at=start.isoformat(),prices=odds,
        market_home_probability=market_probabilities(odds)[0]),None


def run(raw,cohort,team_map,output):
    targets={(r['league'],r['date'],r['home'],r['away']):r for r in [json.loads(s) for s in cohort.read_text(encoding='utf8').splitlines()]}
    tm=json.loads(team_map.read_text(encoding='utf8'));mlb=canonical_map(tm);clean=lambda s:re.sub(r'\s+','',s).casefold()
    alias={l:{clean(k):v for k,v in tm[l].items()} for l in ['KBO','NPB']}
    def canonical(l,n):return mlb(n) if l=='MLB' else alias[l].get(clean(n),n if n in alias[l].values() else None)
    earliest={};latest={};versions=defaultdict(set);at_time=defaultdict(set);counts=Counter();files=[]
    for f in sorted(raw.glob('odds_timeseries*.csv')):
        with f.open('rb') as raw_bytes:digest=hashlib.file_digest(raw_bytes,'sha256').hexdigest()
        files.append(dict(name=f.name,bytes=f.stat().st_size,sha256=digest))
        with f.open(encoding='utf-8-sig') as source:
            for row in csv.DictReader(source):
                quote,reason=quote_fact(row,targets,canonical)
                if quote is None:counts[reason]+=1;continue
                key=tuple(quote['key']);versions[key].add(tuple(quote['prices']))
                at_time[(key,quote['observed_at'])].add(tuple(quote['prices']))
                observed=datetime.fromisoformat(quote['observed_at'])
                if key not in earliest or observed<datetime.fromisoformat(earliest[key]['observed_at']):earliest[key]=quote
                if key not in latest or observed>datetime.fromisoformat(latest[key]['observed_at']):latest[key]=quote
                counts['accepted_quote_rows']+=1
    facts=[]
    ambiguous={k for (k,ts),prices in at_time.items() if len(prices)>1}
    counts['fixtures_with_conflicting_same_timestamp_prices']=len(ambiguous)
    for key,first in earliest.items():
        if key in ambiguous:continue
        last=latest[key];t=targets[key]
        facts.append(dict(league=key[0],date=key[1],fixture_id=t['fixture_id'],home=t['home'],away=t['away'],
            first=first,last=last,distinct_prices=len(versions[key]),delta_home_probability=last['market_home_probability']-first['market_home_probability'],
            target=dict(t,prices=last['prices'],order=['home','away']),hg=t['hg'],ag=t['ag']))
    report=dict(source_files=files,filter_counts=dict(counts),matched_fixtures=dict(Counter(r['league'] for r in facts)),results={})
    for league in ['KBO','NPB','MLB']:
        rows=[r for r in facts if r['league']==league]
        if not rows:continue
        p=np.array([r['last']['market_home_probability'] for r in rows]);v=np.array([r['target']['vote_counts'].get('home',0)/r['target']['voters'] for r in rows]);baseline=.9*p+.1*v
        delta=np.array([r['delta_home_probability'] for r in rows]);changed=np.abs(delta)>=.01;direction=np.where(delta>=0,1.,0.)
        masks={'all':np.ones(len(rows),dtype=bool),'any_nonzero_move':np.abs(delta)>1e-9,'move_at_least_1pp':changed,'vote_agrees_with_move':changed&((v>=.5)==(delta>=0))}
        report['results'][league]={}
        for n,m in masks.items():
            report['results'][league][n]=dict(market=measure(rows,p,m),existing91_reconstructed=measure(rows,baseline,m),vote=measure(rows,v,m))
            if n!='all':report['results'][league][n]['movement_direction']=measure(rows,direction,m)
    (output/'price-movement-facts.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in facts),encoding='utf8',newline='\n')
    (output/'price-movement-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['raw','cohort','team-map','output']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.raw,a.cohort,a.team_map,a.output)
