"""Audit historical revision/price ordering; unchanged price is not proof of inefficiency."""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse,csv,gzip,hashlib,json,math,re,sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from collect_baseball_event_history import read,stamp,fetch
from build_baseball_event_features import completion_bound

KST=timezone(timedelta(hours=9))


def revision_price_window(earlier,later,quotes):
    before=[q for q in quotes if stamp(q['observed_at'])<=stamp(earlier['observed_at'])]
    after=[q for q in quotes if stamp(q['observed_at'])>=stamp(later['observed_at'])]
    if not before or not after:return None
    first=max(before,key=lambda q:q['observed_at']);last=min(after,key=lambda q:q['observed_at'])
    delta=last['market_home_probability']-first['market_home_probability']
    return dict(before=first,after=last,probability_change=delta,small_observed_move=abs(delta)<.01,
                interpretation='descriptive quote response only, not proof that an announcement was unpriced')


def audit_prices(folder,raw,team_map):
    rows=[r for r in read(folder/'event-features.jsonl') if r.get('target')]
    targets={(r['target']['date'],r['target']['home'],r['target']['away']):r for r in rows}
    tm=json.loads(team_map.read_text(encoding='utf8'))['MLB'];clean=lambda s:re.sub(r'\s+','',s).casefold()
    aliases={clean(k):v for k,v in tm.items()};aliases.update({clean(v):v for v in tm.values()})
    quotes=defaultdict(list);counts=Counter();files=[]
    for path in sorted(raw.glob('odds_timeseries*.csv')):
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        files.append(dict(name=path.name,sha256=digest))
        with path.open(encoding='utf-8-sig') as f:
            for q in csv.DictReader(f):
                if q.get('league')!='MLB' or q.get('market_family')!='승패' or q.get('n_way')!='2' or q.get('result')!='경기전':continue
                m=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',q['date_text'])
                if not m:continue
                year=int(q['year'])-int(q['round']=='1' and m[1]=='12')
                start=datetime(year,*map(int,m.groups()),tzinfo=KST)
                h=aliases.get(clean(q['home']));a=aliases.get(clean(q['away']));r=targets.get((start.date().isoformat(),h,a))
                if not r:continue
                observed=stamp(q['ts']);cutoff=stamp(r['proof']['cutoff_at'])
                if observed>=cutoff:counts['after_decision_cutoff']+=1;continue
                if abs((start-stamp(r['start_at'])).total_seconds())>15*60:counts['quote_kickoff_mismatch']+=1;continue
                odds=[float(v) for v in q['odds'].split(',')]
                if len(odds)!=2 or any(not math.isfinite(v) or v<=1 for v in odds):continue
                quotes[r['gamePk']].append(dict(observed_at=observed.isoformat(),prices=odds,
                                                market_home_probability=market_probabilities(odds)[0],source=path.name))
    announcements={}
    for r in read(folder/'announcements.jsonl.gz'):announcements[(r['gamePk'],r['minutes_before'])]=r
    events=[];event_facts=[]
    for r in rows:
        pk=r['gamePk'];late=announcements.get((pk,10));qs=quotes[pk]
        if not late or not late.get('accepted'):continue
        for minutes in [120,30]:
            early=announcements.get((pk,minutes))
            if not early or not early.get('accepted'):continue
            for side in ['home','away']:
                a=early['sides'][side];b=late['sides'][side]
                if not (a['complete_lineup'] and b['complete_lineup']):continue
                replacement=len(set(b['lineup'])-set(a['lineup']))
                order_changed=a['lineup']!=b['lineup']
                starter_change=a.get('starter') is not None and a.get('starter')!=b.get('starter')
                if not (order_changed or starter_change):continue
                window=revision_price_window(early,late,qs)
                events.append(dict(gamePk=pk,date=r['date'],side=side,earlier_minutes=minutes,
                    lineup_replacements=replacement,order_changed=order_changed,probable_starter_change=starter_change,
                    earlier_observed=early['observed_at'],later_observed=late['observed_at'],price_window=window))
        if qs:event_facts.append(dict(gamePk=pk,date=r['date'],quotes=sorted(qs,key=lambda q:q['observed_at'])))
    report=dict(raw_files=files,filter_counts=dict(counts),evaluated_fixtures=len(rows),fixtures_with_quote=sum(bool(quotes[r['gamePk']]) for r in rows),
        revision_observations=len(events),unique_revision_fixtures=len({e['gamePk'] for e in events}),
        unique_revision_fixtures_with_before_after_price=len({e['gamePk'] for e in events if e['price_window']}),events=events)
    (folder/'event-price-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (folder/'event-price-quotes.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in event_facts),encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['raw_files','events']},ensure_ascii=False))


def audit_completion(folder):
    games={}
    for p in (folder/'sources').glob('schedule-*.gz'):
        games.update({g['gamePk']:g for g in read(p)[0]['games'] if completion_bound(g) is not None})
    ordered=sorted(games);sample=[ordered[round(i*(len(ordered)-1)/29)] for i in range(30)]
    def one(pk):
        path=folder/'sources'/f'completion-{pk}.jsonl.gz'
        if path.exists():return read(path)[0]
        try:
            j,meta=fetch(f'https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live',dict(fields='gamePk,liveData,plays,allPlays,about,endTime'))
            end=max(stamp(p['about']['endTime']) for p in j['liveData']['plays']['allPlays'] if p.get('about',{}).get('endTime'))
            bound=completion_bound(games[pk]);r=dict(gamePk=pk,actual_last_play_at=end.isoformat(),derived_bound=bound.isoformat(),
                conservative=bound>=end,margin_minutes=(bound-end).total_seconds()/60,**meta)
        except Exception as e:r=dict(gamePk=pk,reason=str(e)[:150])
        with gzip.open(path,'wt',encoding='utf8',newline='\n') as f:f.write(json.dumps(r)+'\n')
        return r
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,sample))
    report=dict(sample_n=len(results),verified=sum('conservative' in r for r in results),
        violations=[r for r in results if not r.get('conservative',True)],records=results,
        note='sample audit only; does not prove every historical derived completion bound')
    (folder/'completion-audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k!='records'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--kind',choices=['prices','completion'],required=True)
    p.add_argument('--folder',type=Path,required=True);p.add_argument('--raw',type=Path);p.add_argument('--team-map',type=Path)
    a=p.parse_args()
    if a.kind=='prices':audit_prices(a.folder,a.raw,a.team_map)
    else:audit_completion(a.folder)
