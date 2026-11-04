import argparse
import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

URL = 'https://www.kleague.com/api/ddf/match/matchInfo.do'
PRICE_URL = 'https://www.betman.co.kr/buyPsblGame/gameInfoInq.do'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def price_record(row):
    kickoff = datetime.fromisoformat(row['kickoff_kst'])
    if kickoff.tzinfo is None or row['price_observed_at'] is not None:
        raise ValueError('unexpected kickoff or price provenance')
    return {'record_id': 'betman:' + str(row['gmTs']) + ':' + str(row['matchSeq']),
            'kind': 'price', 'sport': 'sc', 'league': 'K' + str(row['league_id']),
            'kickoff': kickoff.isoformat(), 'home': row['league_home_name'], 'away': row['league_away_name'],
            'source_url': PRICE_URL, 'body_sha256': row['response_sha256'],
            'received_at': row['received_at'], 'timestamp_basis': None, 'timing_basis': 'retrospective',
            'payload': {'source_fixture_id': row['fixture_id'], 'home_team_id': row['home_team_id'],
                        'away_team_id': row['away_team_id'], 'prices': row['prices'],
                        'gmId': row['gmId'], 'gmTs': row['gmTs'], 'matchSeq': row['matchSeq'],
                        'score_crosscheck': row['official_score'], 'execution_price_verified': False}}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(year, league, game, evidence):
    if year not in (2025, 2026) or league not in (1, 2) or not 1 <= game <= 999:
        raise ValueError('invalid fixture')
    evidence.mkdir(parents=True, exist_ok=True)
    stem = f'info-{year}-{league}-{game}'
    meta_path, body_path = evidence / (stem + '.json'), evidence / (stem + '.body.gz')
    if meta_path.exists() or body_path.exists():
        if not meta_path.exists() or not body_path.exists(): raise ValueError('incomplete cache')
        meta = json.loads(meta_path.read_text(encoding='utf8'))
        raw = gzip.decompress(body_path.read_bytes())
        if sha(raw) != meta['body_sha256']: raise ValueError('cache hash mismatch')
        return raw, meta, False
    params = {'year': str(year), 'meetSeq': str(league), 'gameId': str(game)}
    req = Request(URL, data=urlencode(params).encode(), headers={'User-Agent':'soccer-research/1.0', 'Referer':'https://www.kleague.com/match.do'})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        raw = response.read(1000001)
        if len(raw) > 1000000: raise ValueError('response too large')
        received = datetime.now(timezone.utc).isoformat()
        meta = {'url': URL, 'params': params, 'status': response.status, 'received_at': received, 'timestamp_basis': 'http_response_body_completed', 'body_sha256': sha(raw)}
    body_path.write_bytes(gzip.compress(raw, mtime=0))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    time.sleep(1)
    return raw, meta, True

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prices', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--spot-check', action='store_true')
    a = p.parse_args()
    with gzip.open(a.prices, 'rt', encoding='utf8') as f:
        rows = [json.loads(line) for line in f if line.strip()]
    records = [price_record(r) for r in rows]
    checks = []
    if a.spot_check:
        for year, league in ((2025,1),(2025,2),(2026,1),(2026,2)):
            row = next(r for r in rows if r['season']==year and r['league_id']==league)
            game = int(row['fixture_id'].split(':')[-1])
            raw, meta, new = fetch(year, league, game, a.evidence)
            obj = json.loads(raw); data = obj.get('data') or {}
            score = [data.get('homeGoal'), data.get('awayGoal')]
            if str(obj.get('resultCode')) != '200' or data.get('gameStatus') != 'FE' or score != row['official_score']:
                raise ValueError('official score mismatch or non-final fixture')
            record = price_record(row)
            record.update(record_id='kleague:result:'+row['fixture_id'], kind='result', source_url=URL,
                          body_sha256=meta['body_sha256'], received_at=meta['received_at'], timestamp_basis=meta.get('timestamp_basis'), timing_basis='retrospective',
                          payload={'source_fixture_id': row['fixture_id'], 'score': score, 'game_status':'FE'})
            records.append(record)
            checks.append({'fixture_id':row['fixture_id'], 'score':score, 'new_request':new, **meta})
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records), encoding='utf8')
    print(json.dumps({'input_prices':len(rows), 'output_records':len(records), 'spot_checks':checks, 'pregame_verified_prices':0, 'missing':'historical receipt, tipster and lineup joins not established'},ensure_ascii=False,indent=2))




# Monthly official schedule pages are the provider's pagination mechanism.
SCHEDULE_URL = 'https://www.kleague.com/getScheduleList.do'
PAGE_URL = 'https://www.kleague.com/match.do'

class Budget:
    def __init__(self, limit):
        if not 0 <= limit <= 60: raise ValueError('request budget must be 0..60')
        self.limit, self.used = limit, 0

    def take(self):
        if self.used >= self.limit: raise RuntimeError('request budget exhausted; rerun to resume')
        self.used += 1


def cached_request(evidence, stem, url, method, params, budget):
    import requests
    evidence.mkdir(parents=True, exist_ok=True)
    meta_path, body_path = evidence / (stem+'.json'), evidence / (stem+'.body.gz')
    if meta_path.exists() or body_path.exists():
        if not meta_path.exists() or not body_path.exists(): raise ValueError('incomplete cache '+stem)
        meta = json.loads(meta_path.read_text(encoding='utf8'))
        raw = gzip.decompress(body_path.read_bytes())
        if sha(raw) != meta['body_sha256'] or meta['url'] != url or meta['params'] != params:
            raise ValueError('cache identity or hash mismatch '+stem)
        return raw, meta
    budget.take()
    with requests.request('POST' if method=='POST_FORM' else method, url, params=params if method=='GET' else None,
                          json=params if method=='POST' else None, data=params if method=='POST_FORM' else None, timeout=(10,15),
                          allow_redirects=False, stream=True,
                          headers={'User-Agent':'soccer-research/1.0','Referer':PAGE_URL}) as response:
        if response.status_code != 200: raise RuntimeError(f'HTTP {response.status_code}; stop')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 3000000: raise ValueError('response too large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        meta = {'url':url,'method':method,'params':params,'status':response.status_code,
                'received_at':datetime.now(timezone.utc).isoformat(),'timestamp_basis':'http_response_body_completed','body_sha256':sha(raw)}
    body_path.write_bytes(gzip.compress(raw,mtime=0))
    meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    time.sleep(0.2)
    return raw, meta


def import_schedule_cache(evidence, year, league, month, prior_root):
    """Import immutable previous raw HTTP and its actual source receipt, never synthesize it."""
    stem=f'schedule-{year}-{league}-{month:02}'
    mp,bp=evidence/(stem+'.json'),evidence/(stem+'.body.gz')
    if mp.exists() or bp.exists(): return False
    if league == 1:
        root=prior_root/'k1lu/experiments/kleague1-official-lineup-20261103'
        if year==2025: root=root/'season-2025'
        path=root/'raw'/f'schedule-{year}-{month:02}.json'
        log=root/'schedule-requests.json'
        if not path.exists() or not log.exists(): return False
        entries=json.loads(log.read_text(encoding='utf8'))
        candidates=[e for e in entries if str((e.get('payload') or e.get('params') or {}).get('year'))==str(year)
                    and str((e.get('payload') or e.get('params') or {}).get('month')).zfill(2)==f'{month:02}' and e.get('status')==200]
        if len(candidates)!=1: raise ValueError('ambiguous schedule receipt')
        old=candidates[0];raw=path.read_bytes();expected=old['sha256']
    else:
        root=prior_root/'k2lu/experiments/kleague2-official-lineup-20261103/cohort/raw'
        path=root/f'schedule-{year}-{month:02}.gz'; log=root/f'schedule-{year}-{month:02}.json'
        if not path.exists() or not log.exists(): return False
        old=json.loads(log.read_text(encoding='utf8'));raw=gzip.decompress(path.read_bytes());expected=old['source_sha256']
    if sha(raw)!=expected: raise ValueError('prior schedule hash mismatch')
    params={'leagueId':str(league),'teamId':'','ticketStatus':'','year':str(year),'month':f'{month:02}','ticketYn':'N'}
    # Record original request separately: prior K1 parameter conventions differ.
    meta={'url':SCHEDULE_URL,'method':'POST','params':params,'status':200,'received_at':old['received_at'],
          'body_sha256':expected,'imported_from':str(path),'original_request':old.get('params',old.get('payload'))}
    evidence.mkdir(parents=True,exist_ok=True);bp.write_bytes(gzip.compress(raw,mtime=0))
    mp.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return True


def parse_official_page(raw, fixture):
    import re
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(raw.decode('utf8'),'html.parser')
    for selector, expected in (('#year',fixture['year']),('#meetSeq',fixture['leagueId']),('#gameId',fixture['gameId'])):
        nodes=soup.select(selector+' option[selected]')
        if len(nodes)!=1 or nodes[0].get('value')!=str(expected): raise ValueError('page fixture identity mismatch')
    teams=soup.select('.game-info > .team')
    if len(teams)!=2: raise ValueError('page teams missing')
    ids=[]
    for team in teams:
        found=[x for x in team.get('class',[]) if re.fullmatch(r'K\d+',x)]
        if len(found)!=1: raise ValueError('ambiguous team identity')
        ids.append(found[0])
    if ids!=[fixture['homeTeam'],fixture['awayTeam']]: raise ValueError('team identity mismatch')
    date_node=soup.select_one('.team-versus .versus p')
    if not date_node or fixture['gameDate'].replace('.','/') not in date_node.get_text(' ',strip=True):
        raise ValueError('fixture date mismatch')
    block=soup.select_one('.match-lineup-player .player')
    sides=block.find_all('div',recursive=False) if block else []
    groups=[]
    for side,tid in zip(sides,ids):
        boxes=side.find_all('div',class_='box',recursive=False)
        if not boxes: continue
        players=[]
        for tr in boxes[0].select('tr[onclick]'):
            m=re.fullmatch(r"playerDetailPop\('([^']+)',\s*'([^']+)'\)",tr.get('onclick','').strip())
            if not m: continue
            cells=tr.find_all('td',recursive=False)
            if m[1]!=tid or not re.fullmatch(r'\d{8}',m[2]) or len(cells)<3: raise ValueError('invalid player identity')
            players.append({'player_id':m[2],'name':cells[2].get_text(strip=True),'number':cells[0].get_text(strip=True),'position':cells[1].get_text(strip=True)})
        groups.append({'team_id':tid,'starters':players})
    if len(groups)==2 and all(len(g['starters'])==11 and len({p['player_id'] for p in g['starters']})==11 for g in groups):
        return 'complete_starting_eleven', groups
    # Absence from a rendered page is not proof of provider publication policy.
    if not any(g['starters'] for g in groups): return 'not_displayed_on_official_page', []
    return 'partial_or_unparseable', groups


def observation_id(kind, fixture_id, meta):
    identity = json.dumps([kind, fixture_id, meta['received_at'], meta['body_sha256']], ensure_ascii=False, separators=(',', ':')).encode('utf8')
    return f'kleague:{kind}:{fixture_id}:' + sha(identity)


def season_main():
    p=argparse.ArgumentParser(description='Bounded resumable K League official schedule and lineup collection')
    p.add_argument('--season-collect',action='store_true')
    p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prior-root',type=Path,default=Path('C:/Users/user/Documents/Codex'))
    p.add_argument('--years',nargs='+',type=int,choices=(2025,2026),default=[2025,2026])
    p.add_argument('--leagues',nargs='+',type=int,choices=(1,2),default=[1,2])
    p.add_argument('--max-requests',type=int,default=60)
    p.add_argument('--probe-unfinished',action='store_true',help='inspect one unfinished official match page')
    p.add_argument('--forecast-fixture',help='unfinished source fixture ID kleague:YEAR:LEAGUE:GAME')
    p.add_argument('--capture-id',help='unique forward observation ID; same ID resumes immutable cached capture')
    p.add_argument('--refresh',action='store_true',help='append a new forward snapshot under capture-id')
    p.add_argument('--max-lineups',type=int,default=4,help='maximum completed fixture pages processed this invocation; rerun with larger value to resume')
    a=p.parse_args();budget=Budget(a.max_requests)
    if not 0<=a.max_lineups<=2000: raise ValueError('invalid lineup limit')
    import re
    if a.refresh and (not a.probe_unfinished or not a.capture_id): raise ValueError('--refresh requires --probe-unfinished and --capture-id')
    if a.capture_id and (not a.refresh or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',a.capture_id)): raise ValueError('capture-id requires refresh and safe identifier')
    fixtures={};source={};imports=0
    current_kst=datetime.now(timezone(timedelta(hours=9)))
    current_month=(current_kst.year,current_kst.month)
    for year in sorted(set(a.years)):
        for league in sorted(set(a.leagues)):
            for month in range(1,13):
                imports+=import_schedule_cache(a.evidence,year,league,month,a.prior_root)
                params={'leagueId':str(league),'teamId':'','ticketStatus':'','year':str(year),'month':f'{month:02}','ticketYn':'N'}
                stem=f'schedule-{year}-{league}-{month:02}'
                refresh_month=a.refresh and ((year,month)>=current_month or (a.evidence/(stem+'-'+a.capture_id+'.json')).exists() or (a.evidence/(stem+'-'+a.capture_id+'.body.gz')).exists())
                if not refresh_month:
                    raw,meta=cached_request(a.evidence,stem,SCHEDULE_URL,'POST',params,budget)
                    # An explicitly targeted unfinished fixture may have been postponed in a past month.
                    if a.refresh and a.forecast_fixture:
                        old=json.loads(raw); old_rows=(old.get('data') or {}).get('scheduleList')
                        if not isinstance(old_rows,list): raise ValueError('invalid schedule')
                        refresh_month=any(f"kleague:{r.get('year')}:{r.get('leagueId')}:{r.get('gameId')}"==a.forecast_fixture and (r.get('gameStatus')!='FE' or r.get('endYn')!='Y') for r in old_rows)
                if refresh_month:
                    raw,meta=cached_request(a.evidence,stem+'-'+a.capture_id,SCHEDULE_URL,'POST',params,budget)
                obj=json.loads(raw);rows=(obj.get('data') or {}).get('scheduleList')
                if str(obj.get('resultCode'))!='200' or not isinstance(rows,list): raise ValueError('invalid schedule')
                for f in rows:
                    if f.get('year')!=year or f.get('leagueId')!=league or f.get('meetSeq')!=league or str(f.get('gameDate',''))[5:7]!=f'{month:02}': continue
                    key=(year,league,f['gameId'])
                    if key in fixtures and fixtures[key]!=f: raise ValueError('conflicting fixture')
                    fixtures[key]=f;source[key]=meta
    records=[];counts={};lineups=0
    cohorts={}
    for key,f in sorted(fixtures.items()):
        if f.get('gameStatus')=='FE' and f.get('endYn')=='Y': cohorts.setdefault(key[:2],[]).append(key)
    ordered=[]
    while any(cohorts.values()):
        for group in sorted(cohorts):
            if cohorts[group]: ordered.append(cohorts[group].pop(0))
    selected=set(ordered[:a.max_lineups])
    probe=next((key for key,f in sorted(fixtures.items()) if f.get('gameStatus')!='FE' or f.get('endYn')!='Y'),None) if a.probe_unfinished else None
    if a.forecast_fixture:
        if not a.probe_unfinished or not re.fullmatch(r'kleague:(2025|2026):[12]:[0-9]+',a.forecast_fixture): raise ValueError('invalid forecast fixture')
        parts=a.forecast_fixture.split(':');probe=tuple(map(int,parts[1:]))
        if probe not in fixtures: raise ValueError('forecast fixture absent from refreshed schedules')
    for key,f in sorted(fixtures.items()):
        year,league,gid=key; m=source[key]
        kickoff=datetime.fromisoformat(f['gameDate'].replace('.','-')+'T'+f['gameTime']+':00+09:00').isoformat()
        fid=f'kleague:{year}:{league}:{gid}'
        base={'sport':'sc','league':f'K{league}','kickoff':kickoff,'home':f['homeTeamName'],'away':f['awayTeamName']}
        rec={'record_id':observation_id('fixture',fid,m),'kind':'fixture',**base,'source_url':SCHEDULE_URL,'body_sha256':m['body_sha256'],'received_at':m['received_at'],'timestamp_basis':m.get('timestamp_basis'),'timing_basis':'retrospective' if datetime.fromisoformat(m['received_at'])>=datetime.fromisoformat(kickoff) else 'actual_receipt','payload':{'source_fixture_id':fid,'home_team_id':f['homeTeam'],'away_team_id':f['awayTeam'],'game_status':f.get('gameStatus'),'end_yn':f.get('endYn'),'round_id':f.get('roundId')}}
        records.append(rec)
        bucket=counts.setdefault(f'{year}-K{league}',{'fixtures':0,'finished':0,'lineups':0,'missing_lineups':0})
        bucket['fixtures']+=1
        if f.get('gameStatus')=='FE' and f.get('endYn')=='Y':
            bucket['finished']+=1
            if type(f.get('homeGoal')) is int and type(f.get('awayGoal')) is int:
                records.append({**rec,'record_id':observation_id('result',fid,m),'kind':'result','timing_basis':'retrospective','payload':{'source_fixture_id':fid,'score':[f['homeGoal'],f['awayGoal']]}})
            if key in selected:
                params={'year':str(year),'leagueId':str(league),'meetSeq':str(league),'gameId':str(gid)}
                page,pm=cached_request(a.evidence,f'match-{year}-{league}-{gid}',PAGE_URL,'GET',params,budget)
                status,teams=parse_official_page(page,f);lineups+=1
                if status=='complete_starting_eleven':bucket['lineups']+=1
                records.append({**rec,'record_id':observation_id('lineup',fid,pm),'kind':'lineup','source_url':PAGE_URL,'body_sha256':pm['body_sha256'],'received_at':pm['received_at'],'timestamp_basis':pm.get('timestamp_basis'),'timing_basis':'retrospective','payload':{'source_fixture_id':fid,'lineup_status':status,'teams':teams,'published_at':None,'pregame_receipt_verified':False}})
        elif key == probe:
            params={'year':str(year),'leagueId':str(league),'meetSeq':str(league),'gameId':str(gid)}
            suffix='-'+a.capture_id if a.refresh else ''
            page,pm=cached_request(a.evidence,f'forecast-page-{year}-{league}-{gid}{suffix}',PAGE_URL,'GET',params,budget)
            status,teams=parse_official_page(page,f)
            if status == 'complete_starting_eleven': status = 'complete_displayed_eleven_confirmation_unverified'
            api_params={'year':str(year),'meetSeq':str(league),'gameId':str(gid)}
            api_raw,api_meta=cached_request(a.evidence,f'forecast-info-{year}-{league}-{gid}{suffix}',URL,'POST_FORM',api_params,budget)
            api=json.loads(api_raw)
            if str(api.get('resultCode'))!='200' or not isinstance(api.get('data'),dict): raise ValueError('invalid official match info')
            records.append({**rec,'record_id':observation_id('lineup',fid,pm),'kind':'lineup','source_url':PAGE_URL,'body_sha256':pm['body_sha256'],'received_at':pm['received_at'],'timestamp_basis':pm.get('timestamp_basis'),'timing_basis':'actual_receipt' if datetime.fromisoformat(pm['received_at'])<datetime.fromisoformat(kickoff) else 'retrospective','payload':{'source_fixture_id':fid,'lineup_status':status,'teams':teams,'published_at':None,'pregame_receipt_verified':False,'official_info':{'source_url':URL,'body_sha256':api_meta['body_sha256'],'received_at':api_meta['received_at'],'timestamp_basis':api_meta.get('timestamp_basis'),'game_status':api['data'].get('gameStatus'),'lineup_field_present':any('lineup' in k.lower() for k in api['data'])},'note':'page display and API response only; not proof of publication status at other channels'}})
    for bucket in counts.values(): bucket['missing_lineups']=bucket['finished']-bucket['lineups']
    if a.refresh and probe and not (fixtures[probe].get('gameStatus')=='FE' and fixtures[probe].get('endYn')=='Y'):
        observation=next(r for r in records if r['kind']=='lineup' and r['payload']['source_fixture_id']==f'kleague:{probe[0]}:{probe[1]}:{probe[2]}')
        obs_path=a.evidence/('observation-'+a.capture_id+'.json')
        encoded=(json.dumps(observation,ensure_ascii=False,sort_keys=True)+'\n').encode('utf8')
        if obs_path.exists():
            if obs_path.read_bytes()!=encoded: raise ValueError('capture-id already used for another observation')
        else: obs_path.write_bytes(encoded)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in records),encoding='utf8')
    print(json.dumps({'new_http_requests':budget.used,'imported_cached_months':imports,'lineup_pages_processed':lineups,'coverage':counts,'records':len(records),'note':'missing lineups include unrequested pages; no pregame receipt inferred'},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    import sys
    season_main() if '--season-collect' in sys.argv else main()
