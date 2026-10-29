"""Local MLB official appearance logs, completion bounds and historical announcements."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse, gzip, hashlib, json, time

import requests

UTC = timezone.utc
GROUPS = {'hitting', 'pitching'}
STAT_KEYS = ['atBats','hits','baseOnBalls','hitByPitch','sacFlies','totalBases','strikeOuts',
             'outs','earnedRuns','numberOfPitches','gamesStarted','saves','holds','battersFaced',
             'homeRuns','runs','plateAppearances']
FIELDS = 'roster,person,id,fullName,stats,type,group,displayName,splits,date,gameType,stat,team,game,gamePk,' + ','.join(STAT_KEYS)


def read(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf8') as f:
        return [json.loads(s) for s in f if s.strip()]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf8', newline='\n') as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')


def fetch(url, params=None):
    error = None
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, timeout=35)
            r.raise_for_status()
            return r.json(), dict(url=r.url, response_sha256=hashlib.sha256(r.content).hexdigest(),
                                  collected_at=datetime.now(UTC).isoformat())
        except (requests.RequestException, ValueError) as e:
            error = e
            if not attempt: time.sleep(.5)
    raise error


def roster_job(year, team, output):
    path = output/'sources'/f'logs-{year}-{team}.jsonl.gz'
    if path.exists(): return read(path)[0]
    url = f'https://statsapi.mlb.com/api/v1/teams/{team}/roster'
    params = dict(rosterType='fullSeason', season=year,
        hydrate=f'person(stats(group=[hitting,pitching],type=gameLog,season={year},gameType=[R,F,D,L,W]))', fields=FIELDS)
    try:
        j, provenance = fetch(url, params)
        rows = []
        for entry in j.get('roster', []):
            player = entry['person']
            for group in player.get('stats', []):
                kind = group.get('group', {}).get('displayName')
                if kind not in GROUPS: continue
                for split in group.get('splits', []):
                    if split.get('team', {}).get('id') != team or split.get('gameType') not in {'R','F','D','L','W'}: continue
                    rows.append(dict(player=player['id'], name=player['fullName'], team=team, year=year,
                        kind=kind, date=split['date'], gamePk=split['game']['gamePk'], game_type=split['gameType'],
                        stat={k:v for k,v in split['stat'].items() if k in STAT_KEYS}))
        result = dict(accepted=bool(rows), year=year, team=team, rows=rows, **provenance)
        if not rows: result['reason']='empty_official_appearance_log'
        write(path, result)
        return result
    except Exception as e:
        return dict(accepted=False, year=year, team=team, reason=str(e)[:180])


def collect_logs(output):
    for year in [2024, 2025, 2026]:
        path = output/'sources'/f'schedule-{year}.jsonl.gz'
        if not path.exists():
            j, meta = fetch('https://statsapi.mlb.com/api/v1/schedule', dict(sportId=1,
                startDate=f'{year}-01-01', endDate=f'{year}-12-31', gameTypes='R,F,D,L,W', hydrate='gameInfo'))
            games = []
            for day in j.get('dates', []):
                for g in day['games']:
                    games.append({k:g[k] for k in ['gamePk','gameType','gameDate','officialDate','status','teams','gameInfo',
                        'resumeDate','resumedFrom','rescheduledFrom','rescheduledGameDate'] if k in g})
            write(path, dict(games=games, **meta))
        teams = sorted({s['team']['id'] for g in read(path)[0]['games'] for s in g['teams'].values()})
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(roster_job, year, team, output) for team in teams]
            for i,f in enumerate(as_completed(jobs),1):
                r=f.result()
                if not r['accepted']: print('log failed',year,r['team'],r.get('reason'),flush=True)
                if i%10==0: print('logs',year,i,'/',len(teams),flush=True)


def stamp(value):
    if '_' in value:
        return datetime.strptime(value,'%Y%m%d_%H%M%S').replace(tzinfo=UTC)
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('timestamp_without_timezone')
    return value.astimezone(UTC)


def extract_announcement(j, requested, start):
    observed=stamp(j['metaData']['timeStamp'])
    if observed>requested or observed>=start: raise ValueError('returned_snapshot_after_requested_cutoff')
    if j['gameData']['status']['codedGameState'] not in {'S','P'}: raise ValueError('not_pregame')
    plays=j.get('liveData',{}).get('plays',{})
    all_plays=plays.get('allPlays',[])+([plays['currentPlay']] if plays.get('currentPlay') else [])
    if any(e.get('isPitch') for p in all_plays for e in p.get('playEvents',[])):
        raise ValueError('pitch_already_thrown')
    sides={}
    for side in ['home','away']:
        box=j.get('liveData',{}).get('boxscore',{}).get('teams',{}).get(side,{})
        lineup=box.get('battingOrder',[])
        probable=j['gameData'].get('probablePitchers',{}).get(side,{}).get('id')
        listed=box.get('pitchers',[])
        confirmed=len(listed)==1 and (probable is None or probable==listed[0])
        sides[side]=dict(team=j['gameData']['teams'][side]['id'],
            starter=listed[0] if confirmed else probable,probable_starter=probable,
            starter_kind='pregame_box_listed' if confirmed else 'probable_only',
            lineup=lineup,complete_lineup=len(lineup)==9 and len(set(lineup))==9)
    return dict(observed_at=observed.isoformat(),requested_at=requested.isoformat(),start_at=start.isoformat(),
                sides=sides,accepted=True,source_kind='official_historical_timecode_replay')


def announcement_job(row, minutes, output, confirm=False):
    prefix='confirmation' if confirm else 'announcement'
    path=output/'sources'/f"{prefix}-{row['gamePk']}-{minutes}.jsonl.gz"
    if path.exists(): return read(path)[0]
    start=stamp(row['start_at']);cut=start-timedelta(minutes=minutes)
    url=f"https://statsapi.mlb.com/api/v1.1/game/{row['gamePk']}/feed/live"
    params=dict(timecode=cut.strftime('%Y%m%d_%H%M%S'),fields='gamePk,metaData,timeStamp,gameData,status,codedGameState,teams,home,away,id,probablePitchers,liveData,boxscore,battingOrder,pitchers,plays,allPlays,currentPlay,playEvents,isPitch')
    try:
        j,meta=fetch(url,params)
        if j.get('gamePk')!=row['gamePk']: raise ValueError('wrong_fixture')
        fact=extract_announcement(j,cut,start)
        result=dict(gamePk=row['gamePk'],minutes_before=minutes,**fact,**meta)
    except Exception as e:
        result=dict(gamePk=row['gamePk'],minutes_before=minutes,accepted=False,reason=str(e)[:180])
    write(path,result)
    return result


def collect_announcements(output, features, confirm=False):
    rows=[r for r in read(features) if r['role']=='evaluation' or (r['role']=='train' and 'historical_prices' in r)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(announcement_job,r,m,output,confirm) for r in rows for m in ([10] if confirm else [120,30,10])]
        for i,f in enumerate(as_completed(futures),1):
            r=f.result()
            if i%100==0: print('announcements',i,'/',len(futures),flush=True)


def reconcile_logs(output):
    games={}
    for p in (output/'sources').glob('schedule-*.gz'):games.update({g['gamePk']:g for g in read(p)[0]['games']})
    sums=defaultdict(float)
    for p in (output/'sources').glob('logs-*.gz'):
        for r in read(p)[0].get('rows',[]):sums[r['gamePk'],r['team'],r['kind']]+=r['stat'].get('runs',0)
    bad=set();checks=0
    for pk,g in games.items():
        if g.get('status',{}).get('codedGameState')!='F':continue
        for side in ['home','away']:
            tid=g['teams'][side]['team']['id'];other='away' if side=='home' else 'home'
            for kind in GROUPS:
                expected=g['teams'][side if kind=='hitting' else other].get('score')
                if expected is None:continue
                checks+=1
                if sums.get((pk,tid,kind))!=expected:bad.add(pk)
    def one(pk):
        p=output/'sources'/f'repair-{pk}.jsonl.gz'
        if p.exists():return read(p)[0]
        try:
            j,meta=fetch(f'https://statsapi.mlb.com/api/v1/game/{pk}/boxscore',dict(fields='teams,home,away,team,id,players,person,fullName,stats,batting,pitching,'+','.join(STAT_KEYS)))
            rows=[];g=games[pk]
            for side in ['home','away']:
                box=j['teams'][side];tid=box['team']['id']
                for pinfo in box['players'].values():
                    for kind,key in [('hitting','batting'),('pitching','pitching')]:
                        stats=pinfo.get('stats',{}).get(key,{})
                        if not stats:continue
                        rows.append(dict(player=pinfo['person']['id'],name=pinfo['person']['fullName'],team=tid,
                            year=int(g['officialDate'][:4]),kind=kind,date=g['officialDate'],gamePk=pk,game_type=g['gameType'],
                            stat={k:v for k,v in stats.items() if k in STAT_KEYS}))
            totals=defaultdict(float)
            for r in rows:totals[r['team'],r['kind']]+=r['stat'].get('runs',0)
            for side in ['home','away']:
                tid=g['teams'][side]['team']['id'];other='away' if side=='home' else 'home'
                for kind in GROUPS:
                    if totals.get((tid,kind))!=g['teams'][side if kind=='hitting' else other]['score']:raise ValueError('repair_box_score_mismatch')
            result=dict(gamePk=pk,accepted=True,rows=rows,**meta)
        except Exception as e:result=dict(gamePk=pk,accepted=False,reason=str(e)[:160])
        write(p,result);return result
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,sorted(bad)))
    report=dict(team_game_group_checks=checks,fixtures_requiring_repair=len(bad),
        repaired=sum(r['accepted'] for r in results),failed=[{k:v for k,v in r.items() if k!='rows'} for r in results if not r['accepted']])
    (output/'log-reconciliation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind',choices=['logs','announcements','confirmations','reconcile'],required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--features',type=Path)
    a=p.parse_args()
    if a.kind=='logs': collect_logs(a.output)
    elif a.kind=='reconcile':reconcile_logs(a.output)
    else: collect_announcements(a.output,a.features,a.kind=='confirmations')
