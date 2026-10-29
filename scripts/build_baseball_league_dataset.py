"""Compact local historical facts. Does not modify runtime DB or fetch production."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

LEAGUES=['KBO','NPB','MLB']
KBO_OPENING={2023:'2023-04-01',2024:'2024-03-23',2025:'2025-03-22',2026:'2026-03-28'}
MLB_EN='Los Angeles Dodgers|Arizona Diamondbacks|New York Yankees|San Francisco Giants|Miami Marlins|New York Mets|Seattle Mariners|Cleveland Guardians|Cincinnati Reds|Pittsburgh Pirates|Los Angeles Angels|Kansas City Royals|Minnesota Twins|Texas Rangers|Philadelphia Phillies|Houston Astros|Chicago White Sox|San Diego Padres|Colorado Rockies|Boston Red Sox|Baltimore Orioles|St. Louis Cardinals|Toronto Blue Jays|Chicago Cubs|Milwaukee Brewers|Washington Nationals|Atlanta Braves|Tampa Bay Rays|Detroit Tigers|Athletics'.split('|')
MLB_KO='LA다저스 애리조나 뉴욕양키스 샌프란시스코 마이애미 뉴욕메츠 시애틀 클리블랜드 신시내티 피츠버그 LA에인절스 캔자스시티 미네소타 텍사스 필라델피아 휴스턴 시카고W 샌디에이고 콜로라도 보스턴 볼티모어 세인트루이스 토론토 시카고컵스 밀워키 워싱턴 애틀랜타 탬파베이 디트로이트 애슬레틱스'.split()
NPB_EN='Yomiuri Giants|Hanshin Tigers|Hiroshima Toyo Carp|Yokohama DeNA BayStars|Chunichi Dragons|Tokyo Yakult Swallows|Orix Buffaloes|Fukuoka SoftBank Hawks|Tohoku Rakuten Golden Eagles|Saitama Seibu Lions|Chiba Lotte Marines|Hokkaido Nippon-Ham Fighters'.split('|')
NPB_KO='요미우리 한신 히로시마 요코하마 주니치 야쿠르트 오릭스 소프트뱅크 라쿠텐 세이부 지바롯데 닛폰햄'.split()
NPB_FULL='요미우리 자이언츠|한신 타이거스|히로시마 도요 카프|요코하마 DeNA 베이스타스|주니치 드래곤즈|야쿠르트 스왈로즈|오릭스 버팔로스|소프트뱅크 호크스|라쿠텐 골든이글스|세이부 라이온즈|지바 롯데 마린스|니혼햄 파이터스'.split('|')


def norm(s):return re.sub(r'\s+','',str(s)).casefold()


def aliases(team_map):
    out={l:{norm(k):v for k,v in team_map[l].items()} for l in LEAGUES}
    for l,english,korean in [('MLB',MLB_EN,MLB_KO),('NPB',NPB_EN,NPB_KO)]:
        out[l].update({norm(k):v for k,v in zip(english,korean)})
        out[l].update({norm(k):k for k in korean})
    out['NPB'].update({norm(k):v for k,v in zip(NPB_FULL,NPB_KO)})
    out['MLB'].update({norm('오클랜드'):'애슬레틱스',norm('Oakland Athletics'):'애슬레틱스'})
    for name in ['KIA','KT','LG','NC','SSG','두산','롯데','삼성','키움','한화']:
        out['KBO'][norm(name)]=name
    for full in ['KIA 타이거즈','KT 위즈','LG 트윈스','NC 다이노스','SSG 랜더스','두산 베어스','롯데 자이언츠','삼성 라이온즈','키움 히어로즈','한화 이글스']:
        out['KBO'][norm(full)]=full.split()[0]
    return out


def fixture_key(r):return r['league'],r['date'],r['home'],r['away']


def valid_score(x):return type(x) is int and 0<=x<100


def build(raw,archive,year_folder,mlb_official):
    amap=aliases(json.loads((raw.parent/'processed/team_map.json').read_text(encoding='utf8')))
    groups=defaultdict(list);excluded=Counter()
    def canonical(league,value):return amap[league].get(norm(value))
    def add(league,day,home,away,hg,ag,source,**extra):
        if league=='KBO' and day<KBO_OPENING[int(day[:4])]:
            excluded['kbo_before_regular_season']+=1;return
        h,a=canonical(league,home),canonical(league,away)
        if not h or not a or h==a or not valid_score(hg) or not valid_score(ag):
            excluded['invalid_identity_or_score:'+source]+=1;return
        r=dict(league=league,date=day,home=h,away=a,hg=hg,ag=ag,source=source,**extra)
        groups[fixture_key(r)].append(r)
    for league in LEAGUES:
        rows=json.loads((raw/(league.lower()+'_starters.json')).read_text(encoding='utf8'))
        for r in rows:
            inning=re.match(r'(\d+)회',r.get('status') or '')
            if not inning or int(inning[1])<9:
                excluded['raw_without_late_inning:'+league]+=1;continue
            add(league,r['date'],r['home'],r['away'],r['home_score'],r['away_score'],'archived_starter_score',
                actual_starters=[r.get('home_starter') or '',r.get('away_starter') or ''],raw_id=r['gameId'])
    frame=pd.read_csv(archive/'data/processed/games.csv.tmp')
    for r in frame.to_dict('records'):
        if r['league'] not in LEAGUES or r['market_family']!='승패' or r['is_void'] or r['result'] not in ['홈승','홈패','무승부']:
            continue
        h=re.fullmatch(r'(.+?)\s+(\d+)',str(r['home']).strip());a=re.fullmatch(r'(\d+)\s+(.+)',str(r['away']).strip())
        dt=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',r['date_text'])
        if not h or not a or not dt:continue
        year=int(r['year'])-int(int(r['round'])==1 and dt[1]=='12')
        add(r['league'],f'{year}-{dt[1]}-{dt[2]}',h[1],a[2],int(h[2]),int(a[1]),'archived_proto_result')
    base=archive/'experiments/tipster-year-20261028'
    for filename in ['overseas/official_matches.json','korean/official_kbo_results.json','korean/official_results.json']:
        for r in json.loads((base/filename).read_text(encoding='utf8')):
            league=r['league']
            if league not in LEAGUES or league=='MLB':continue
            day=r.get('match_date',r.get('date'))
            add(league,day,r['home'],r['away'],r['home_score'],r['away_score'],'official_result',
                official_id=r.get('official_id'),url=r.get('official_url',r.get('source_url')))
    mlb_pairs=set()
    for r in [json.loads(s) for s in mlb_official.read_text(encoding='utf8').splitlines()]:
        if r['game_type'] not in {'R','F','D','L','W'}:
            raise ValueError('MLB history contains spring/exhibition/unsupported game type')
        day=datetime.fromisoformat(r['start_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat()
        add('MLB',day,r['home'],r['away'],r['home_score'],r['away_score'],'official_result',
            official_id='MLB:'+str(r['gamePk']),url=r['official_url'],game_type=r['game_type'])
        mlb_pairs.add(('MLB',day,canonical('MLB',r['home']),canonical('MLB',r['away'])))
    master={}
    for key,batch in groups.items():
        if key[0]=='MLB' and key not in mlb_pairs:
            excluded['mlb_outside_official_regular_postseason']+=1;continue
        official=[r for r in batch if r['source']=='official_result']
        if len({r['official_id'] for r in official if r.get('official_id')})>1:
            excluded['ambiguous_multiple_official_fixtures_same_day']+=1;continue
        trusted=official or [r for r in batch if r['source']=='archived_proto_result'] or batch
        scores={(r['hg'],r['ag']) for r in trusted}
        if len(scores)!=1:
            excluded['ambiguous_day_pair_or_score_conflict']+=1;continue
        row=dict(trusted[0]);score=next(iter(scores))
        matching=[r for r in batch if (r['hg'],r['ag'])==score]
        starter_pairs={tuple(r['actual_starters']) for r in matching if r.get('actual_starters')}
        row['actual_starters']=list(next(iter(starter_pairs))) if len(starter_pairs)==1 else ['','']
        row['sources']=sorted({r['source'] for r in matching})
        excluded['discarded_lower_priority_score_mismatch']+=sum((r['hg'],r['ag'])!=score for r in batch)
        master[key]=row
    score_rows={r['fixture_id']:r for r in [json.loads(s) for s in (year_folder/'strict.jsonl').read_text(encoding='utf8').splitlines()]}
    targets=[]
    for e in json.loads((year_folder/'strict-results.json').read_text(encoding='utf8'))['event_ledger']:
        if e['league'] not in LEAGUES or any(v is None for v in e['picks'].values()):continue
        s=score_rows[e['fixture_id']];l=e['league'];h,a=canonical(l,e['home']),canonical(l,e['away'])
        if not h or not a:raise ValueError('unmatched cohort team')
        target=dict(e,home=h,away=a,date=e['match_date'],hg=s['home_score'],ag=s['away_score'])
        key=fixture_key(target)
        old=master.get(key)
        if old and (old['hg'],old['ag'])!=(target['hg'],target['ag']):raise ValueError('cohort/source score disagreement')
        if old is None:
            master[key]=dict(league=l,date=target['date'],home=h,away=a,hg=target['hg'],ag=target['ag'],
                source='cohort_official_score',actual_starters=['',''],sources=['cohort_official_score'])
            excluded['cohort_score_anchor_added']+=1
        targets.append(target)
    # Attach post-game facts only for updating history AFTER that date, never current inputs.
    for kind,filename in [('pitchers','kbo_baseball_2023_2026.json'),('batters','kbo_batters_indiv_2023_2026.json')]:
        for r in json.loads((raw/'detail'/filename).read_text(encoding='utf8')).values():
            h,a=canonical('KBO',r['home']),canonical('KBO',r['away'])
            key=('KBO',r['date'],h,a);row=master.get(key)
            if not row or (row['hg'],row['ag'])!=(r['home_score'],r['away_score']):continue
            row[kind]=[r.get('data',{}).get(side) or [] for side in ['home','away']]
    history=sorted(master.values(),key=lambda r:fixture_key(r))
    for r in history:
        if 'pitchers' in r:
            r['pitchers']=[[[str(p.get('name') or ''),str(p.get('inn') or '0'),*[float(p.get(k) or 0) for k in ['r','bb','kk','hit','bf']]] for p in ps] for ps in r['pitchers']]
        if 'batters' in r:
            r['batters']=[[[str(p.get('code') or ''),int(p.get('order') or 0),*[float(p.get(k) or 0) for k in ['ab','hit','bb','hr','d','t']]] for p in ps] for ps in r['batters']]
    return history,targets,dict(excluded)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['raw','archive','year-folder','output','mlb-official']:p.add_argument('--'+n,type=Path,required=True)
    args=p.parse_args();history,targets,excluded=build(args.raw,args.archive,args.year_folder,args.mlb_official)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,rs in [('history.jsonl',history),('targets.jsonl',targets)]:
        (args.output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rs),encoding='utf8',newline='\n')
    source_paths=[args.raw/(l.lower()+'_starters.json') for l in LEAGUES]+[
        args.archive/'data/processed/games.csv.tmp',args.raw/'detail/kbo_baseball_2023_2026.json',
        args.raw/'detail/kbo_batters_indiv_2023_2026.json',args.year_folder/'strict.jsonl',args.year_folder/'strict-results.json',args.mlb_official]+[
        args.archive/'experiments/tipster-year-20261028'/f for f in ['overseas/official_matches.json','korean/official_kbo_results.json','korean/official_results.json']]
    metadata=dict(history=len(history),targets=len(targets),excluded=excluded,
        by_league_year=dict(Counter(r['league']+':'+r['date'][:4] for r in history)),
        sources=[dict(name=f.name,sha256_lf=hashlib.sha256(f.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for f in source_paths],
        archived_starter_identity_is_not_pregame_announcement=True,
        history_complete_official_census=False)
    (args.output/'inputs.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in metadata.items() if k!='sources'},ensure_ascii=False))
