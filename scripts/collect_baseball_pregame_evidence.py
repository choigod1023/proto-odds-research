"""Local-only primary-source collection; never treats final boxes as announcements."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
import argparse
import csv
import hashlib
import gzip
import io
import json
import re
import time

import requests
from bs4 import BeautifulSoup

UTC=timezone.utc
NPB_CODES=dict(f='닛폰햄',e='라쿠텐',g='요미우리',d='주니치',h='소프트뱅크',m='지바롯데',
    l='세이부',b='오릭스',s='야쿠르트',c='히로시마',t='한신',db='요코하마')


def read(path):
    if path.exists():text=path.read_text(encoding='utf8')
    elif Path(str(path)+'.gz').exists():
        with gzip.open(str(path)+'.gz','rt',encoding='utf8') as f:text=f.read()
    else:return []
    return [json.loads(s) for s in text.splitlines() if s]


def stamp(text):
    if re.fullmatch(r'\d{8}_\d{6}',text or ''):
        return datetime.strptime(text,'%Y%m%d_%H%M%S').replace(tzinfo=UTC)
    return datetime.fromisoformat(text.replace('Z','+00:00')).astimezone(UTC)


def extract_snapshot(j, cutoff, scheduled_start):
    """Require official as-of timestamp, pregame status, no real pitches, 9 unique batters."""
    observed=stamp(j.get('metaData',{}).get('timeStamp',''))
    if observed>cutoff or observed>=scheduled_start:raise ValueError('snapshot_timestamp_after_cutoff')
    data=j['gameData'];status=data['status']
    if status.get('codedGameState') not in {'S','P'}:raise ValueError('not_scheduled_or_warmup')
    plays=j.get('liveData',{}).get('plays',{})
    all_plays=plays.get('allPlays',[])+([plays['currentPlay']] if plays.get('currentPlay') else [])
    if any(e.get('isPitch') for p in all_plays for e in p.get('playEvents',[])):
        raise ValueError('pitch_already_thrown')
    out={}
    for side in ['home','away']:
        starter=data.get('probablePitchers',{}).get(side,{}).get('id')
        box=j['liveData']['boxscore']['teams'][side]
        order=box.get('battingOrder',[])
        if not starter or len(order)!=9 or len(set(order))!=9:raise ValueError('starter_or_complete_lineup_missing')
        players=box.get('players',{})
        def player(pid):
            p=players.get('ID'+str(pid),{})
            return dict(id=int(pid),name=p.get('person',{}).get('fullName',''),
                batting=p.get('seasonStats',{}).get('batting',{}),pitching=p.get('seasonStats',{}).get('pitching',{}))
        out[side]=dict(starter=player(starter),lineup=[player(pid) for pid in order])
    return dict(snapshot_at=observed.isoformat(),cutoff_at=cutoff.isoformat(),start_at=scheduled_start.isoformat(),
        status=status['detailedState'],source_kind='official_historical_timecode_replay',sides=out)


def get(url):
    r=requests.get(url,timeout=30)
    r.raise_for_status()
    return r


def mlb_one(job):
    start=stamp(job['start_at']);cutoff=start-timedelta(minutes=10)
    code=cutoff.strftime('%Y%m%d_%H%M%S')
    url=f"https://statsapi.mlb.com/api/v1.1/game/{job['gamePk']}/feed/live?timecode={code}"
    try:
        response=get(url);j=response.json()
        if j.get('gamePk')!=job['gamePk']:raise ValueError('different_fixture_returned')
        fact=extract_snapshot(j,cutoff,start)
        return dict(job,**fact,url=url,response_sha256=hashlib.sha256(response.content).hexdigest(),
            collected_at=datetime.now(UTC).isoformat(),accepted=True)
    except Exception as e:
        return dict(**job,url=url,accepted=False,reason=str(e)[:160],error_type=type(e).__name__)


def collect_mlb(prior,output,sample=300):
    facts=read(prior/'mlb-official-final.jsonl');targets=read(prior/'targets.jsonl')
    by_id={r['gamePk']:r for r in facts};jobs={}
    for year in [2024,2025]:
        candidates=sorted([r for r in facts if r['start_at'].startswith(str(year)) and r['start_at'][5:10]<='09-30'],key=lambda r:(r['start_at'],r['gamePk']))
        # Deterministic date-spread sample, chosen without looking at score or price.
        indices=sorted({round(i*(len(candidates)-1)/(sample-1)) for i in range(min(sample,len(candidates)))})
        for i in indices:
            r=candidates[i];jobs[r['gamePk']]=dict(r,role='train' if year==2024 else 'validation')
    for t in targets:
        if t['league']!='MLB':continue
        pk=int(t['fixture_id'].split(':')[-1]);r=by_id.get(pk)
        if r:jobs[pk]=dict(r,role='evaluation',target=t)
    file=output/'mlb-snapshots.jsonl';done={r['gamePk'] for r in read(file)}
    if not file.exists() and Path(str(file)+'.gz').exists():
        with gzip.open(str(file)+'.gz','rb') as f:file.write_bytes(f.read())
    with file.open('a',encoding='utf8',newline='\n') as f,ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(mlb_one,j) for pk,j in jobs.items() if pk not in done]
        for i,future in enumerate(as_completed(futures),1):
            r=future.result();f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
            if i%50==0:print('MLB collected',len(done)+i,'/',len(jobs),flush=True)
    print('MLB finished',len(read(file)),flush=True)


def collect_savant(output):
    for year in [2023,2024,2025]:
        for kind in ['batter','pitcher']:
            file=output/f'savant-{year}-{kind}.json'
            if file.exists():continue
            url=f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type={kind}&year={year}&position=&team=&filterType=bip&min=0&csv=true'
            r=get(url);rows=list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))
            if not rows or 'player_id' not in rows[0] or 'est_woba' not in rows[0]:raise ValueError('unexpected Savant schema')
            file.write_text(json.dumps(dict(url=url,season=year,collected_at=datetime.now(UTC).isoformat(),
                response_sha256=hashlib.sha256(r.content).hexdigest(),rows=rows),ensure_ascii=False)+'\n',encoding='utf8',newline='\n')
    print('Savant six prior-season tables saved',flush=True)


def parse_npb_box(html,url):
    match=re.search(r'/scores/(\d{4})/(\d{2})(\d{2})/([a-z]+)-([a-z]+)-',url)
    if not match or match[4] not in NPB_CODES or match[5] not in NPB_CODES:raise ValueError('not_first_team_fixture')
    soup=BeautifulSoup(html,'lxml');pitchers={}
    # Each first row is the actual starter, not a pregame announcement.
    for table in soup.find_all('table'):
        header=table.find('tr')
        if not header or '投球数' not in header.get_text() or '自責点' not in header.get_text():continue
        heading=table.find_previous(['h3','h4','h5']).get_text(' ',strip=True)
        rows=[]
        for tr in table.find_all('tr'):
            if tr.find_parent('table') is not table:continue
            cells=tr.find_all(['th','td'],recursive=False)
            if len(cells)!=14:continue
            a=cells[1].find('a',href=True)
            if not a:continue
            nums=[c.get_text(' ',strip=True) for c in cells]
            rows.append(dict(name=nums[1],player_url=a['href'],pitches=nums[2],bf=nums[3],innings=nums[4],
                hits=nums[5],hr=nums[6],bb=nums[7],hbp=nums[8],k=nums[9],runs=nums[12],er=nums[13]))
        if rows:pitchers[heading]=rows
    if len(pitchers)!=2:raise ValueError('missing_two_pitcher_tables')
    return dict(league='NPB',date=f'{match[1]}-{match[2]}-{match[3]}',home=NPB_CODES[match[4]],away=NPB_CODES[match[5]],
        source_kind='postgame_official_training_history_only',url=url,pitching=pitchers)


def npb_one(url):
    try:
        r=get(url);r.encoding=r.apparent_encoding;fact=parse_npb_box(r.text,url)
        return dict(**fact,accepted=True,response_sha256=hashlib.sha256(r.content).hexdigest())
    except Exception as e:return dict(url=url,accepted=False,reason=str(e)[:160],error_type=type(e).__name__)


def collect_npb(output):
    links=set()
    for month in range(3,12):
        url=f'https://npb.jp/games/2023/schedule_{month:02d}_detail.html'
        r=get(url);soup=BeautifulSoup(r.content,'lxml')
        for a in soup.find_all('a',href=True):
            m=re.fullmatch(r'/scores/2023/(\d{4})/([a-z]+)-([a-z]+)-(\d+)/',a['href'])
            if m and m[2] in NPB_CODES and m[3] in NPB_CODES and m[1]>='0330':links.add('https://npb.jp'+a['href']+'box.html')
    file=output/'npb-2023-boxes.jsonl';done={r['url'] for r in read(file)}
    with file.open('a',encoding='utf8',newline='\n') as f,ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(npb_one,url) for url in sorted(links-done)]
        for i,future in enumerate(as_completed(futures),1):
            f.write(json.dumps(future.result(),ensure_ascii=False)+'\n');f.flush()
            if i%100==0:print('NPB collected',len(done)+i,'/',len(links),flush=True)
    print('NPB finished',len(read(file)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prior',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--kind',choices=['mlb','savant','npb'],required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.kind=='mlb':collect_mlb(args.prior,args.output)
    elif args.kind=='savant':collect_savant(args.output)
    else:collect_npb(args.output)
