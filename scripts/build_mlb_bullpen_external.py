"""Frozen MLB bullpen candidate on new timestamped pregame-price fixtures."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse,csv,gzip,hashlib,json,re,shutil,sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities

KST=timezone(timedelta(hours=9))
EN='Los Angeles Dodgers|Arizona Diamondbacks|New York Yankees|San Francisco Giants|Miami Marlins|New York Mets|Seattle Mariners|Cleveland Guardians|Cincinnati Reds|Pittsburgh Pirates|Los Angeles Angels|Kansas City Royals|Minnesota Twins|Texas Rangers|Philadelphia Phillies|Houston Astros|Chicago White Sox|San Diego Padres|Colorado Rockies|Boston Red Sox|Baltimore Orioles|St. Louis Cardinals|Toronto Blue Jays|Chicago Cubs|Milwaukee Brewers|Washington Nationals|Atlanta Braves|Tampa Bay Rays|Detroit Tigers|Athletics'.split('|')
KO='LA다저스 애리조나 뉴욕양키스 샌프란시스코 마이애미 뉴욕메츠 시애틀 클리블랜드 신시내티 피츠버그 LA에인절스 캔자스시티 미네소타 텍사스 필라델피아 휴스턴 시카고W 샌디에이고 콜로라도 보스턴 볼티모어 세인트루이스 토론토 시카고컵스 밀워키 워싱턴 애틀랜타 탬파베이 디트로이트 애슬레틱스'.split()


def read(path):
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf8') as f:return [json.loads(s) for s in f if s.strip()]


def stamp(value):
    v=datetime.fromisoformat(value.replace('Z','+00:00'))
    if v.tzinfo is None:raise ValueError('timestamp_without_zone')
    return v


def bound(g):
    i=g.get('gameInfo',{})
    if g.get('status',{}).get('codedGameState')!='F' or g.get('resumeDate') or g.get('resumedFrom') or not i.get('firstPitch') or not i.get('gameDurationMinutes'):return None
    return stamp(i['firstPitch'])+timedelta(minutes=float(i['gameDurationMinutes'])+float(i.get('delayDurationMinutes',0))+60)


def source_index(folder):
    root=folder/'sources'/'mlb';games={g['gamePk']:g for g in read(root/'schedule-2026.jsonl.gz')[0]['games']}
    repairs=sorted(root.glob('repair-*.gz'));ids={read(p)[0]['gamePk'] for p in repairs};by_team=defaultdict(list)
    for p in sorted(root.glob('logs-*.gz'))+repairs:
        for r in read(p)[0].get('rows',[]):
            if not p.name.startswith('repair-') and r['gamePk'] in ids:continue
            if r['kind']!='pitching' or r['stat'].get('gamesStarted')!=0:continue
            g=games.get(r['gamePk']);end=bound(g) if g else None
            if end is not None:by_team[r['team']].append(dict(r,completed_bound=end,played_at=stamp(g['gameInfo']['firstPitch'])))
    return games,by_team


def features(g,cutoff,by_team):
    day=datetime.fromisoformat(g['officialDate']).date();dates=[(day-timedelta(days=i)).isoformat() for i in [1,2,3]];sides=[];proof={}
    for side in ['home','away']:
        tid=g['teams'][side]['team']['id']
        past=[r for r in by_team.get(tid,[]) if r['date']<g['officialDate'] and r['completed_bound']<cutoff and cutoff-timedelta(days=28)<=r['played_at']]
        y=[r for r in past if r['date']==dates[0]];three=[r for r in past if r['date'] in dates]
        yi={r['player'] for r in y};di={r['player'] for r in past if r['date']==dates[1]};ranks=defaultdict(lambda:[0.,0.])
        for r in past:ranks[r['player']][0]+=r['stat'].get('saves',0)+r['stat'].get('holds',0);ranks[r['player']][1]+=r['stat'].get('outs',0)
        top=sorted(ranks,key=lambda p:(-ranks[p][0],-ranks[p][1],p))[:3]
        total=lambda rows,key:sum(float(r['stat'].get(key,0)) for r in rows)
        sides.append(dict(relief_pitches1=total(y,'numberOfPitches'),relief_pitches3=total(three,'numberOfPitches'),
            relief_consecutive=len(yi&di),relief_top3_used1=sum(p in yi for p in top),
            relief_kbb28=(total(past,'strikeOuts')-total(past,'baseOnBalls')+100*.14)/(total(past,'battersFaced')+100)))
        proof[side]=dict(team=tid,source_game_ids=sorted({r['gamePk'] for r in past}),top3_past_ids=top,
            latest_completion_bound=max((r['completed_bound'] for r in past),default=None).isoformat() if past else None)
    return {k:sides[0][k]-sides[1][k] for k in sides[0]},proof


def prepare(prior,raw,team_map,output):
    target=output/'sources'/'mlb';target.mkdir(parents=True,exist_ok=True)
    for p in (prior/'sources').glob('*.gz'):
        if p.name=='schedule-2026.jsonl.gz' or p.name.startswith('logs-2026-') or (p.name.startswith('repair-') and any(r['year']==2026 for r in read(p)[0].get('rows',[]))):shutil.copyfile(p,target/p.name)
    report=json.loads((prior/'event-results.json').read_text(encoding='utf8'))
    config=report['models'][report['selected']]['final_fit']
    if report['selected']!='bullpen:l1':raise ValueError('unexpected_prior_candidate')
    (output/'frozen-mlb-model.json').write_text(json.dumps(dict(config=config,source_commit='4d1e45e0a47856c13918549ab00142c4ccd38734',source_pr=408),indent=2)+'\n',encoding='utf8',newline='\n')
    original=read(prior/'event-predictions.jsonl');excluded_ids={r['gamePk'] for r in original}
    (output/'excluded-mlb-fixtures.json').write_text(json.dumps(sorted(excluded_ids))+'\n',encoding='utf8',newline='\n')
    games,by_team=source_index(output)
    errors=[]
    for r in original:
        if not r['date'].startswith('2026'):continue
        x,_=features(games[r['gamePk']],stamp(r['proof']['cutoff_at']),by_team)
        errors.append(max(abs(x[n]-r['features'][n]) for n in config['names']))
    if max(errors,default=0)>1e-12:raise ValueError('frozen_feature_definition_not_reproduced')
    clean=lambda s:re.sub(r'\s+','',s).casefold();tm=json.loads(team_map.read_text(encoding='utf8'))['MLB']
    aliases={clean(k):v for k,v in tm.items()};aliases.update({clean(k):v for k,v in zip(EN,KO)});aliases.update({clean(v):v for v in KO})
    aliases[clean('오클랜드')]='애슬레틱스';aliases[clean('Oakland Athletics')]='애슬레틱스'
    canon=lambda s:aliases.get(clean(s));index=defaultdict(list)
    for g in games.values():
        dt=stamp(g['gameDate']).astimezone(KST);h=canon(g['teams']['home']['team']['name']);a=canon(g['teams']['away']['team']['name'])
        if h and a:index[(dt.date().isoformat(),h,a)].append(g)
    candidates=defaultdict(list);counts=Counter();files=[];versions=defaultdict(set)
    for p in sorted(raw.glob('odds_timeseries*.csv')):
        with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        files.append(dict(name=p.name,sha256=digest))
        with p.open(encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                if r.get('league')!='MLB' or r.get('market_family')!='승패' or r.get('n_way')!='2' or r.get('result')!='경기전':continue
                m=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',r['date_text'])
                if not m:continue
                year=int(r['year'])-int(r['round']=='1' and m[1]=='12');start=datetime(year,*map(int,m.groups()),tzinfo=KST)
                if not '2026-07-26'<=start.date().isoformat()<='2026-08-23':continue
                gs=[g for g in index.get((start.date().isoformat(),canon(r['home']),canon(r['away'])),[]) if abs((stamp(g['gameDate'])-start).total_seconds())<=15*60]
                if len(gs)!=1:counts['ambiguous_or_no_official_fixture']+=1;continue
                g=gs[0];pk=g['gamePk']
                if pk in excluded_ids:counts['previous_evaluation_fixture']+=1;continue
                observed=stamp(r['ts'])
                actual=stamp(g['gameInfo']['firstPitch']) if g.get('gameInfo',{}).get('firstPitch') else stamp(g['gameDate'])
                if not start-timedelta(hours=24)<=observed<min(start,stamp(g['gameDate']),actual)-timedelta(minutes=10):counts['outside_pregame_decision_window']+=1;continue
                if g.get('status',{}).get('codedGameState')!='F':counts['no_final_score']+=1;continue
                prices=[float(v) for v in r['odds'].split(',')]
                if len(prices)!=2 or any(not 1<v<10000 for v in prices):counts['invalid_price']+=1;continue
                versions[pk,observed.isoformat()].add(tuple(prices))
                candidates[pk].append(dict(observed_at=observed.isoformat(),prices=prices,source=p.name))
    conflicts={pk for (pk,ts),ps in versions.items() if len(ps)>1};rows=[]
    for pk,quotes in sorted(candidates.items()):
        if pk in conflicts:counts['conflicting_same_timestamp_prices']+=1;continue
        q=max(quotes,key=lambda q:stamp(q['observed_at']));g=games[pk];cut=stamp(q['observed_at']);x,proof=features(g,cut,by_team)
        rows.append(dict(league='MLB',gamePk=pk,date=stamp(g['gameDate']).astimezone(KST).date().isoformat(),
            home=canon(g['teams']['home']['team']['name']),away=canon(g['teams']['away']['team']['name']),hg=g['teams']['home']['score'],ag=g['teams']['away']['score'],
            historical_prices=q['prices'],market_probability=market_probabilities(q['prices'])[0],decision_at=q['observed_at'],quote=q,
            scheduled_start=g['gameDate'],features=x,proof=proof,reference_kind='market_only_no_historical_tipster_input'))
    (output/'mlb-external-features.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8',newline='\n')
    audit=dict(new_fixtures=len(rows),excluded_prior_evaluation=len(excluded_ids),filter_counts=dict(counts),raw_files=files,
        original_feature_replay_n=len(errors),original_feature_max_error=max(errors,default=0),
        reference='market only; not an independent 9:1 comparison')
    (output/'mlb-external-audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in audit.items() if k!='raw_files'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['prior','raw','team-map','output']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();prepare(a.prior,a.raw,a.team_map,a.output)
