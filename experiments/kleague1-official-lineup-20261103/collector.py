"""Bounded, resumable research capture of official K League match pages.

Official match page exposes lineup tables and its updateMatchInfo function calls
/api/ddf/match/matchInfo.do with year, meetSeq and gameId. Captures are current,
retrospective observations and do not authenticate historical pregame receipt.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

BASE=Path(__file__).resolve().parent
LOG=BASE/'requests.json'

def write_json(path,value):
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8'))

def capture(targets,max_fixtures):
    if not 1<=max_fixtures<=4:raise ValueError('max-fixtures must be 1..4')
    entries=json.loads(LOG.read_text(encoding='utf8')) if LOG.exists() else []
    if any(e.get('status') in (401,403,429) for e in entries):raise ValueError('previous access restriction; do not retry automatically')
    (BASE/'raw').mkdir(exist_ok=True)
    for year,gid in targets[:max_fixtures]:
        if year not in (2023,2024,2025,2026) or not (1<=gid<=999):raise ValueError('invalid target')
        jobs=[('GET',f'https://www.kleague.com/match.do?year={year}&leagueId=1&gameId={gid}&meetSeq=1&startTabNum=2',None,f'raw/match-{year}-{gid}.html.gz',3000000),
          ('POST','https://www.kleague.com/api/ddf/match/matchInfo.do',{'year':str(year),'meetSeq':'1','gameId':str(gid)},f'raw/info-{year}-{gid}.json',1000000)]
        for method,url,params,name,limit in jobs:
            prior=[e for e in entries if e.get('raw_file')==name]
            if prior:
                if prior[-1].get('status')!=200:raise ValueError('cached response was not successful')
                data=(BASE/name).read_bytes();data=gzip.decompress(data) if name.endswith('.gz') else data
                if hashlib.sha256(data).hexdigest()!=prior[-1]['sha256']:raise ValueError('cached evidence hash mismatch')
                continue
            e={'method':method,'url':url,'requested_at':dt.datetime.now(dt.timezone.utc).isoformat(),'max_bytes':limit,'timeout_seconds':15}
            if params:e['params']=params
            try:
                with requests.request(method,url,data=params,timeout=15,allow_redirects=False,stream=True) as r:
                    e['status']=r.status_code;buf=[];size=0
                    for chunk in r.iter_content(65536):
                        size+=len(chunk)
                        if size>limit:raise ValueError('response_too_large')
                        buf.append(chunk)
                    raw=b''.join(buf);e.update(received_at=dt.datetime.now(dt.timezone.utc).isoformat(),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),raw_file=name)
                    (BASE/name).write_bytes(gzip.compress(raw,mtime=0) if name.endswith('.gz') else raw)
            except (requests.RequestException,ValueError) as exc:
                e.update(error=type(exc).__name__,ended_at=dt.datetime.now(dt.timezone.utc).isoformat())
            entries.append(e);write_json(LOG,entries)
            if e.get('status')!=200 or e.get('error'):raise RuntimeError('request failed or restricted; no automatic retry')
            time.sleep(1)

def player_rows(table,team_id):
    out=[]
    for tr in table.select('tr[onclick*="playerDetailPop"]'):
        match=re.search(r"playerDetailPop\('([^']+)',\s*'([^']+)'\)",tr.get('onclick',''))
        cells=tr.find_all('td',recursive=False)
        if not match or match[1]!=team_id or len(cells)<3:raise ValueError('player row identity or shape mismatch')
        number,position,name=(c.get_text(' ',strip=True) for c in cells[:3])
        out.append({'player_id':match[2],'name':name,'shirt_number':number,'position':position})
    return out

def parse_pair(page,info,year,gid):
    text=page.decode('utf8',errors='strict');s=BeautifulSoup(text,'html.parser')
    for selector,value in (('#year',year),('#gameId',gid),('#meetSeq',1)):
        selected=s.select_one(selector+' option[selected]')
        if not selected or selected.get('value')!=str(value):raise ValueError('fixture identity mismatch')
    tab=s.select_one('#tab02 .match-lineup-player');versus=s.select_one('.team-versus')
    if not tab or not versus:raise ValueError('lineup or fixture missing')
    sides=versus.select(':scope > .team');teams=[]
    for side in sides:
        badge=side.select_one('i.ic-emblem');ids=[x for x in badge.get('class',[]) if re.fullmatch(r'K\d+',x)] if badge else []
        if len(ids)!=1:raise ValueError('ambiguous team id')
        teams.append(ids[0])
    if len(teams)!=2 or teams[0]==teams[1]:raise ValueError('invalid teams')
    game=s.select_one('.game-info');names=[e.get_text(' ',strip=True) for e in game.select(':scope > .team > p')]
    boxes=tab.select(':scope > .player > div')
    if len(names)!=2 or len(boxes)!=2:raise ValueError('unexpected team table structure')
    lineups=[]
    for i,box in enumerate(boxes):
        tables=box.select(':scope > .box > table')
        if len(tables)!=2:raise ValueError('unexpected starting and bench table count')
        bench_label=tables[1].parent.find('p',recursive=False)
        if not bench_label or bench_label.get_text(' ',strip=True)!='대기 명단':raise ValueError('bench label missing')
        starters=player_rows(tables[0],teams[i]);bench=player_rows(tables[1],teams[i]);ids=[p['player_id'] for p in starters+bench]
        if len(starters)!=11 or len(ids)!=len(set(ids)):raise ValueError('missing or duplicated starters')
        pitch=s.select('#tab02 .lineup-wrap .lineup > .'+('home' if i==0 else 'away')+' [onclick*=playerDetailPop]')
        pitch_ids=[]
        for node in pitch:
            item=re.search(r"playerDetailPop\('([^']+)',\s*'([^']+)'\)",node.get('onclick',''))
            if not item or item[1]!=teams[i]:raise ValueError('pitch player identity mismatch')
            pitch_ids.append(item[2])
        if len(pitch_ids)!=11 or set(pitch_ids)!={x['player_id'] for x in starters}:raise ValueError('pitch and starting table disagree')
        formation=re.search(r"\$\(\"\."+('hFormation' if i==0 else 'aFormation')+r"\"\)\.text\(separateNumber\('([^']+)'\)\)",text)
        lineups.append({'side':'home' if i==0 else 'away','team_id':teams[i],'team_name':names[i],'starters':starters,'bench':bench,'formation':formation.group(1) if formation else None})
    timestamp=versus.select_one('.versus > p');match=re.search(r'(\d{4})/(\d{2})/(\d{2}).*?(\d{2}):(\d{2})',timestamp.get_text(' ',strip=True) if timestamp else '')
    if not match or int(match[1])!=year:raise ValueError('missing kickoff date')
    doc=json.loads(info);data=doc.get('data') or {}
    if str(doc.get('resultCode'))!='200' or data.get('gameStatus')!='FE':raise ValueError('match info not final')
    if any(type(data.get(k)) is not int or data[k]<0 for k in ('homeGoal','awayGoal')):raise ValueError('missing scores')
    substitutions=[];seen_events=0
    for period in ('firstHalf','secondHalf','EfirstHalf','EsecondHalf'):
        for event in data.get(period) or []:
            seen_events+=1
            if str(event.get('year'))!=str(year) or str(event.get('gameId'))!=str(gid) or str(event.get('leagueId'))!='1':raise ValueError('event fixture mismatch')
            if event.get('eventName')=='교체':
                if event.get('teamId') not in teams:raise ValueError('substitution team mismatch')
                substitutions.append({k:event.get(k) for k in ('teamId','playerId','playerName','playerId2','playerName2','halfType','timeMin','timeSec','gubunSub','gubunSubNm')})
    if not seen_events:raise ValueError('match info identity cannot be verified')
    return {'fixture_id':f'kleague:{year}:1:{gid}','league':'KLEAGUE1','day':f'{match[1]}-{match[2]}-{match[3]}',
      'kickoff_local_display':f'{match[4]}:{match[5]}','kickoff_timezone':'unverified','scores':{'home':data['homeGoal'],'away':data['awayGoal']},
      'match_status':data['gameStatus'],'lineups':lineups,'substitutions':substitutions,
      'lineup_status':'official_retrospective_starting_xi','announced_at':None,'actual_pregame_receipt':None,'actual_pregame_receipt_verified':False,
      'provenance_note':'current post-match page, recorded starting XI; original announcement and actual pregame receipt unknown'}

def build():
    entries=json.loads(LOG.read_text(encoding='utf8')); pages={};infos={}
    for e in entries:
        if e.get('status')!=200 or not e.get('raw_file'):continue
        data=(BASE/e['raw_file']).read_bytes();data=gzip.decompress(data) if e['raw_file'].endswith('.gz') else data
        if hashlib.sha256(data).hexdigest()!=e['sha256']:raise ValueError('evidence hash mismatch')
        params=parse_qs(urlsplit(e['url']).query) if e['method']=='GET' else {k:[v] for k,v in e['params'].items()}
        key=(int(params['year'][0]),int(params['gameId'][0]));(pages if e['method']=='GET' else infos)[key]=(data,e)
    results=[];failures=[]
    for year,gid in sorted(pages.keys() | infos.keys()):
        try:
            if (year,gid) not in pages or (year,gid) not in infos:raise ValueError('missing source pair')
            page,pe=pages[(year,gid)];info,ie=infos[(year,gid)];r=parse_pair(page,info,year,gid)
            r['evidence']={'page':pe,'match_info':ie};results.append(r)
        except (ValueError,KeyError,TypeError,UnicodeError) as exc:failures.append({'year':year,'game_id':gid,'reason':str(exc)})
    (BASE/'fixtures.jsonl').write_bytes(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in results).encode('utf8'))
    coverage={'attempted_fixtures':len(pages.keys() | infos.keys()),'parsed_fixtures':len(results),'both_sides_xi_11':len(results),'player_starters':sum(len(t['starters']) for r in results for t in r['lineups']),
      'bench_players':sum(len(t['bench']) for r in results for t in r['lineups']),'formation_sides':sum(t['formation'] is not None for r in results for t in r['lineups']),
      'substitution_events':sum(len(r['substitutions']) for r in results),'historical_publication_time_verified':0,'historical_actual_pregame_receipt_verified':0,
      'schema_version':'1.0','league':'KLEAGUE1','source_url':'https://www.kleague.com/match.do','observation_method':'official page GET and page-declared matchInfo read-only POST; current receipt',
      'source_archives_sha256':{e['raw_file']:hashlib.sha256((BASE/e['raw_file']).read_bytes()).hexdigest() for e in entries if e.get('raw_file')},
      'request_log_sha256':hashlib.sha256(LOG.read_bytes()).hexdigest(),
      'failures':failures,'requests_total':len(entries),'fixtures_sha256':hashlib.sha256((BASE/'fixtures.jsonl').read_bytes()).hexdigest(),
      'limitations':['Retrospective current official page; lineup publication/receipt time not established.','Lineup is recorded starting XI, not proof of an earlier official announcement or absence report.','Substitution playerId/playerId2 are retained in source order; direction is not inferred.','Kickoff timezone, historical prices and pre-match player features remain unverified.']}
    write_json(BASE/'manifest.json',coverage);print(json.dumps(coverage,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',action='store_true');p.add_argument('--target',action='append',default=[],help='year:game_id');p.add_argument('--max-fixtures',type=int,default=1);a=p.parse_args()
    if a.capture:
        targets=[tuple(map(int,x.split(':'))) for x in a.target]
        if not targets:raise ValueError('explicit official fixture target required')
        capture(targets,a.max_fixtures)
    build()
