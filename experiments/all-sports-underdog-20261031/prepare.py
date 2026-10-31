"""Normalize all available domestic primary markets; source files read only."""
from collections import Counter, defaultdict
import gzip, hashlib, json, math, os, re
from pathlib import Path
import sys
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'src'))


def write_gz(path,rows):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with Path(path).open('wb') as out:
        with gzip.GzipFile(fileobj=out,mode='wb',filename='',mtime=0) as z:
            for r in rows:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())


def normalize(sport,league,kickoff,home,away,odds,y,source):
    t=pd.Timestamp(kickoff)
    if pd.isna(t) or not home or not away or sport not in ('sc','bs','bk','vl'):raise ValueError('bad fixture')
    odds=list(map(float,odds));k=3 if sport=='sc' else 2
    if len(odds)!=k or not all(math.isfinite(x) and x>1 for x in odds) or not 1<=sum(1/x for x in odds)<=1.40:raise ValueError('bad odds')
    if int(y)!=y or not 0<=int(y)<k:raise ValueError('bad label')
    signature=[sport,str(league),t.isoformat(),str(home),str(away)]
    identity=hashlib.sha256(json.dumps(signature,ensure_ascii=False).encode()).hexdigest()[:24]
    return dict(id='domestic|'+identity,cohort='domestic_archive',sport=sport,league=str(league),
                day=t.date().isoformat(),kickoff=t.isoformat(),kickoff_precision='minute_timezone_uncertified',
                home=str(home),away=str(away),odds=odds,y=int(y),sources=[source])


def merge(rows,rejected=()):
    seen={};bad=set(rejected);repeats=0
    for row in rows:
        key=row['id']
        if key not in seen:seen[key]=row;continue
        repeats+=1;old=seen[key]
        if (old['odds'],old['y'])!=(row['odds'],row['y']):bad.add(key)
        old['sources']=sorted(set(old['sources']+row['sources']))
    return sorted([r for k,r in seen.items() if k not in bad],key=lambda r:(r['day'],r['kickoff'],r['id'])),{'duplicate_rows':repeats,'conflicting_fixtures_excluded':len(bad&set(seen)),
        'source_conflict_ids_propagated':len(set(rejected)),'cached_conflict_resurrections_prevented':sum(k in seen and seen[k]['sources']==['legacy_primary_cache'] for k in rejected)}


def raw_primary_conflicts(raw):
    """Keep known raw conflicts even if the upstream loader drops both offers."""
    variants=defaultdict(set)
    for r in raw.to_dict('records'):
        s=r['sport'];k=int(r['n_way'])
        if not (s=='sc' and r['market_family']=='승무패' and k==3 or s in ('bs','bk','vl') and r['market_family']=='승패' and k==2):continue
        if r['is_void'] or r['result'] not in ('홈승','홈패','무승부'):continue
        tm=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',str(r['date_text']))
        home=re.match(r'^(.+?)\s+(-?\d+)\s*$',str(r['home']));away=re.match(r'^(-?\d+)\s+(.+?)\s*$',str(r['away']))
        if not tm or not home or not away:continue
        mm,dd,hh,minute=map(int,tm.groups());year=int(r['year'])-(1 if int(r['round'])==1 and mm==12 else 0)
        y=0 if r['result']=='홈승' else 1 if r['result']=='무승부' else k-1
        try:
            odds=list(map(float,str(r['odds']).split(',')));time=pd.Timestamp(year=year,month=mm,day=dd,hour=hh,minute=minute)
            row=normalize(s,r['league'],time,home.group(1),away.group(2),odds,y,'raw_conflict_scan')
        except (ValueError,TypeError):continue
        variants[row['id']].add((tuple(odds),y,int(home.group(2)),int(away.group(1))))
    return {identity for identity,values in variants.items() if len(values)>1}


def prepare(cache,csv,output):
    if os.environ.get('PROODD_DB_PATH'):raise ValueError('offline only: configured DB forbidden')
    from accuracy_formula_lab import load_unique_market_rows
    from matches import load_matches
    cache=Path(cache);csv=Path(csv);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    # Trusted local artifact created by this repository's earlier experiment.
    # Discard its old derived features and reconstruct new ones in common.py.
    d=pd.read_pickle(cache);rows=[];excluded=Counter()
    for r in d.itertuples(index=False):
        expected=0 if r.outcome==1 else 1 if r.outcome==.5 else 2 if r.sport=='sc' else 1
        if r.y!=expected or (r.sport!='sc' and r.outcome==.5):excluded['cache_score_conflict']+=1;continue
        try:rows.append(normalize(r.sport,r.league,r.kickoff,r.home_team,r.away_team,r.odds,r.y,'legacy_primary_cache'))
        except ValueError:excluded['cache_invalid']+=1
    raw=pd.read_csv(csv,low_memory=False)
    records,quality=load_unique_market_rows(csv)
    matches=load_matches(path=csv)
    score={(r.sport,r.league,pd.Timestamp(r.kickoff).isoformat(),r.home_team,r.away_team):r.outcome for r in matches.itertuples(index=False)}
    for r in records:
        key=r['key'];sport=r['sport'];family=key[5]
        if not (sport=='sc' and family=='승무패' and r['n_way']==3 or sport in ('bs','bk','vl') and family=='승패' and r['n_way']==2):
            excluded['non_primary_market']+=1;continue
        t,s,l,h,a=key[:5];outcome=score.get((s,l,pd.Timestamp(t).isoformat(),h,a))
        if outcome is None:excluded['missing_physical_score']+=1;continue
        expected=0 if outcome==1 else 1 if outcome==.5 else 2 if sport=='sc' else 1
        if expected!=r['winner'] or outcome==.5 and sport!='sc':excluded['raw_score_conflict']+=1;continue
        try:rows.append(normalize(s,l,t,h,a,r['odds'],r['winner'],'broad_raw_archive'))
        except ValueError:excluded['raw_invalid']+=1
    clean,duplicates=merge(rows,rejected=raw_primary_conflicts(raw))
    write_gz(output/'domestic.jsonl.gz',clean)
    audit={'source_hashes':{cache.name:hashlib.sha256(cache.read_bytes()).hexdigest(),csv.name:hashlib.sha256(csv.read_bytes()).hexdigest()},
           'source_cache_rows':len(d),'source_raw_rows':len(raw),'raw_families':raw.market_family.value_counts().to_dict(),
           'raw_quality':quality,'exclusions':dict(excluded),**duplicates,
           'fixtures':len(clean),'range':[min(r['day'] for r in clean),max(r['day'] for r in clean)],
           'sport_counts':dict(Counter(r['sport'] for r in clean)),
           'sport_year_counts':dict(Counter(r['sport']+'/'+r['day'][:4] for r in clean)),
           'leagues':sorted({r['league'] for r in clean}),
           'price_execution_certified':0,'warning':'Settled archive, not certified pregame receipts; exact physical fixture conflicts removed across sources, old derived feature columns unused.'}
    (output/'domestic-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:audit[k] for k in ('fixtures','range','sport_counts','conflicting_fixtures_excluded')},ensure_ascii=False))
    return clean,audit


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--cache',required=True);p.add_argument('--raw',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();prepare(a.cache,a.raw,a.output)
