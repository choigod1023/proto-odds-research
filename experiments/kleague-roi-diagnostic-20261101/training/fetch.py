"""Bounded one-shot collection of K League official public monthly results.

No retries, at most one request in flight, 24 POSTs, 15s timeout, 3MB cap.
Calls correspond to onChangedNew in the previously saved official schedule page.
Existing request log forbids a second capture; parsing can be rerun offline.
"""
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import requests

BASE=Path(__file__).resolve().parent
URL='https://www.kleague.com/getScheduleList.do'
LIMIT=3_000_000

def write_json(path,obj):
    path.write_bytes((json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf8'))

def capture():
    path=BASE/'requests.json'
    if path.exists(): raise RuntimeError('request log already exists; do not silently repeat collection')
    entries=[];write_json(path,entries)
    for year in (2023,2024):
        for month in range(1,13):
            payload={'leagueId':1,'teamId':'','year':str(year),'month':f'{month:02d}','ticketYn':''}
            e={'url':URL,'method':'POST','payload':payload,'requested_at':dt.datetime.now(dt.timezone.utc).isoformat(),'max_bytes':LIMIT,'timeout_seconds':15,'raw_file':None}
            try:
                with requests.post(URL,json=payload,timeout=15,stream=True,allow_redirects=False) as response:
                    e['http_status']=response.status_code; chunks=[];size=0
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>LIMIT: raise ValueError('response_too_large')
                        chunks.append(chunk)
                    data=b''.join(chunks)
                    e['received_at']=dt.datetime.now(dt.timezone.utc).isoformat();e['bytes']=len(data);e['sha256']=hashlib.sha256(data).hexdigest()
                    name=f'raw/{year}-{month:02d}.json';(BASE/name).write_bytes(data);e['raw_file']=name
                    try:
                        doc=json.loads(data); rows=doc.get('data',{}).get('scheduleList'); e['result_code']=doc.get('resultCode');e['row_count']=len(rows) if isinstance(rows,list) else None
                        e['status']='ok' if response.status_code==200 and str(e['result_code'])=='200' and isinstance(rows,list) else 'failed_response'
                    except (ValueError,AttributeError,TypeError):e['status']='failed_parse'
            except (requests.RequestException,ValueError) as error:
                e['status']='failed';e['error_type']=type(error).__name__;e['ended_at']=dt.datetime.now(dt.timezone.utc).isoformat()
            entries.append(e);write_json(path,entries)
            print(f"{year}-{month:02d}: {e['status']} rows={e.get('row_count')} http={e.get('http_status')}",flush=True)
            if e.get('http_status') in (401,403):
                print('Access denied; no further requests.',flush=True);return

def process(entries):
    records=[]; month_reports=[]; names=defaultdict(set)
    for e in entries:
        month=f"{e['payload']['year']}-{e['payload']['month']}";info={'month':month,'request_status':e['status'],'rows':e.get('row_count'),'excluded':{}}
        if e['status']!='ok': month_reports.append(info);continue
        b=(BASE/e['raw_file']).read_bytes()
        if hashlib.sha256(b).hexdigest()!=e['sha256']: raise ValueError('source hash mismatch: '+month)
        rows=json.loads(b)['data']['scheduleList']
        for i,r in enumerate(rows):
            if not isinstance(r,dict): records.append({'month':month,'row':i,'reason':'malformed_row'});continue
            year=e['payload']['year']; date=str(r.get('gameDate','')); valid=(str(r.get('year'))==year and str(r.get('leagueId'))=='1' and str(r.get('meetSeq'))=='1' and date.startswith(month.replace('-','.')))
            home,away=r.get('homeTeam'),r.get('awayTeam'); hn,an=r.get('homeTeamName'),r.get('awayTeamName')
            if valid and all(isinstance(x,str) and x for x in (home,away,hn,an)):
                names[home].add(hn);names[away].add(an)
            records.append({'month':month,'row':i,'valid_scope':valid,'data':r,'source':e})
        month_reports.append(info)
    conflicts={id_:sorted(v) for id_,v in names.items() if len(v)>1}
    ids=Counter((str(x['data'].get('year')),str(x['data'].get('meetSeq')),str(x['data'].get('gameId'))) for x in records if x.get('valid_scope'))
    fixtures=[]; excluded=[]
    for x in records:
        if 'data' not in x: excluded.append(x);continue
        r=x['data']; e=x['source']; y=str(r.get('year')); gid=r.get('gameId'); home=r.get('homeTeam');away=r.get('awayTeam');score=(r.get('homeGoal'),r.get('awayGoal'))
        if str(r.get('year'))!=e['payload']['year']: reason='wrong_year'
        elif str(r.get('leagueId'))!='1' or str(r.get('meetSeq'))!='1': reason='different_competition'
        elif not str(r.get('gameDate','')).startswith(x['month'].replace('-','.')): reason='wrong_month'
        elif not x['valid_scope']: reason='invalid_scope'
        elif gid is None or not all(isinstance(z,str) and z for z in (home,away,r.get('homeTeamName'),r.get('awayTeamName'))) or home==away: reason='missing_or_invalid_identity'
        elif ids[(y,str(r.get('meetSeq')),str(gid))]!=1: reason='duplicate_fixture_id'
        elif home in conflicts or away in conflicts: reason='team_name_changed'
        elif r.get('gameStatus')!='FE' or r.get('endYn')!='Y': reason='not_final_or_cancelled'
        elif any(type(s) is not int or s<0 for s in score): reason='invalid_score'
        else: reason=None
        if reason:
            excluded.append({'month':x['month'],'row':x['row'],'gameId':gid,'reason':reason,'gameStatus':r.get('gameStatus'),'endYn':r.get('endYn')});continue
        day=r['gameDate'].replace('.','-');time=r.get('gameTime')
        try: dt.date.fromisoformat(day)
        except ValueError:
            excluded.append({'month':x['month'],'row':x['row'],'gameId':gid,'reason':'invalid_day'});continue
        try: dt.datetime.strptime(time,'%H:%M')
        except (TypeError,ValueError): time=None
        fixtures.append({'fixture_id':f"kleague:{y}:1:{gid}",'league':'KLEAGUE1','year':int(y),'game_id':gid,'round_id':r.get('roundId'),'home_team_id':home,'home':r['homeTeamName'],'away_team_id':away,'away':r['awayTeamName'],'day':day,'kickoff':day+'T'+time+':00' if time else None,'kickoff_timezone':'unverified','scores':{'home':score[0],'away':score[1]},'source_status':r['gameStatus'],'source_end_yn':r['endYn'],'source_url':URL,'source_evidence_id':e['raw_file'],'received_at':e['received_at'],'source_sha256':e['sha256'],'observation_method':'direct_read_only_post_response_bytes'})
    for report in month_reports:
        report['excluded']=dict(Counter(r['reason'] for r in excluded if r['month']==report['month']))
        report['accepted']=sum(f['day'].startswith(report['month']) for f in fixtures)
    return fixtures,excluded,conflicts,month_reports

def build():
    entries=json.loads((BASE/'requests.json').read_text(encoding='utf8'))
    fixtures,excluded,conflicts,months=process(entries)
    (BASE/'fixtures.jsonl').write_bytes(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in fixtures).encode('utf8'))
    manifest={'schema_version':'1.0','league':'KLEAGUE1','source_url':URL,'observation_method':'read-only official schedule POST; current actual response receipt, not pregame evidence',
      'request_cap':24,'max_in_flight':1,'max_bytes_per_response':LIMIT,'timeout_seconds':15,'no_retry':True,
      'request_count':len(entries),'months':months,'missing_months':[f'{y}-{m:02d}' for y in (2023,2024) for m in range(1,13) if f'{y}-{m:02d}' not in {x['month'] for x in months}],
      'counts':{'raw_rows':sum(e.get('row_count') or 0 for e in entries),'accepted':len(fixtures),'accepted_by_year':dict(Counter(str(f['year']) for f in fixtures)),'excluded_by_reason':dict(Counter(x['reason'] for x in excluded))},
      'team_name_conflicts':conflicts,'excluded_rows':excluded,'source_sha256s':{e['raw_file']:e['sha256'] for e in entries if e.get('raw_file')},
      'requests_sha256':hashlib.sha256((BASE/'requests.json').read_bytes()).hexdigest(),'fixtures_sha256':hashlib.sha256((BASE/'fixtures.jsonl').read_bytes()).hexdigest(),
      'limitations':['Retrospective official record, not actual historical pregame receipt.','Timezone and exact UTC kickoff not independently certified.','No prices, model fitting or ROI calculation.']}
    write_json(BASE/'manifest.json',manifest)
    print(json.dumps(manifest['counts'],ensure_ascii=True),flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--capture',action='store_true');a=p.parse_args()
    if a.capture:capture()
    build()
