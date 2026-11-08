"""2025-only bounded capture with immutable derived snapshots; no model fit."""
import argparse,datetime as dt,gzip,hashlib,importlib.util,json,os
from pathlib import Path

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent
spec=importlib.util.spec_from_file_location('k1_lineup_collector',ROOT/'collector.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
collector.BASE=BASE;collector.LOG=BASE/'requests.json'

def atomic_log(path,value):
    data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_bytes(data);os.replace(temporary,path)

collector.write_json=atomic_log

def reject_unlogged_responses():
    entries=json.loads((BASE/'requests.json').read_text(encoding='utf8'))
    registered={e.get('raw_file') for e in entries if e.get('raw_file')}
    present={str(p.relative_to(BASE)).replace('\\','/') for pattern in ('match-2025-*.html.gz','info-2025-*.json') for p in (BASE/'raw').glob(pattern)}
    if present-registered:raise ValueError('unlogged response files require manual review; refusing overwrite')

def digest(b):return hashlib.sha256(b).hexdigest()
def save_once(p,b):
    if p.exists():
        if p.read_bytes()!=b:raise ValueError('immutable snapshot differs: '+str(p))
    else:p.write_bytes(b)

def build():
    plan=json.loads((BASE/'plan.json').read_text(encoding='utf8'));entries=json.loads((BASE/'requests.json').read_text(encoding='utf8'))
    files={}
    for e in entries:
        name=e.get('raw_file')
        if not name or e.get('status')!=200:continue
        raw=(BASE/name).read_bytes();body=gzip.decompress(raw) if name.endswith('.gz') else raw
        if digest(body)!=e['sha256'] or len(body)!=e['bytes']:raise ValueError('response checksum mismatch: '+name)
        if name in files:raise ValueError('duplicate response evidence: '+name)
        files[name]=(body,e)
    fixtures=[];timelines=[];failures=[];pending=[]
    for target in plan['targets']:
        gid=target['game_id'];pk=f'raw/match-2025-{gid}.html.gz';ik=f'raw/info-2025-{gid}.json'
        if pk not in files or ik not in files:pending.append(target['fixture_id']);continue
        try:
            page,pe=files[pk];info,ie=files[ik];r=collector.parse_pair(page,info,2025,gid);side=r['lineups'];score=r['scores']
            if (r['day'],side[0]['team_id'],side[1]['team_id'],score['home'],score['away'])!=(target['day'],target['home_team_id'],target['away_team_id'],target['home_goal'],target['away_goal']):raise ValueError('official schedule mismatch')
            r['evidence']={'page':pe,'match_info':ie};fixtures.append(r)
            data=json.loads(info)['data']
            timelines.append({'fixture_id':r['fixture_id'],'source_evidence_id':ik,'received_at':ie['received_at'],
              'source_sha256':ie['sha256'],'event_fields_are_retrospective_not_pregame_features':True,
              'periods':{k:data.get(k) for k in ('firstHalf','secondHalf','EfirstHalf','EsecondHalf')}})
        except (ValueError,KeyError,TypeError,UnicodeError) as ex:failures.append({'fixture_id':target['fixture_id'],'reason':str(ex)})
    request_bytes=(BASE/'requests.json').read_bytes();key=digest(request_bytes)[:20];snapshot=BASE/'snapshots'/key;snapshot.mkdir(parents=True,exist_ok=True)
    fb=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in fixtures).encode('utf8')
    tb=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in timelines).encode('utf8')
    save_once(snapshot/'fixtures.jsonl',fb);save_once(snapshot/'timeline.jsonl',tb);save_once(snapshot/'requests.json',request_bytes)
    cov={'schema_version':'1.0','season':2025,'scheduled':228,'source_official_completed':228,'lineup_and_result_verified':len(fixtures),
      'starters':sum(len(s['starters']) for r in fixtures for s in r['lineups']),'bench':sum(len(s['bench']) for r in fixtures for s in r['lineups']),
      'formation_sides':sum(s['formation'] is not None for r in fixtures for s in r['lineups']),'substitution_events':sum(len(r['substitutions']) for r in fixtures),
      'pending_count':len(pending),'pending_fixture_ids':pending,'failed_count':len(failures),'failures':failures,
      'historical_publication_time_verified':0,'historical_actual_pregame_receipt_verified':0,
      'request_count':len(entries),'request_log_sha256':digest(request_bytes),'plan_sha256':digest((BASE/'plan.json').read_bytes()),
      'schedule_requests_sha256':digest((BASE/'schedule-requests.json').read_bytes()),
      'fixtures_sha256':digest(fb),'timeline_sha256':digest(tb),'snapshot_key':key,
      'source_archives_sha256':{name:digest((BASE/name).read_bytes()) for name in sorted({e['raw_file'] for e in entries if e.get('raw_file')})},
      'schedule_sha256s':{p.name:digest(p.read_bytes()) for p in sorted((BASE/'raw').glob('schedule-2025-*.json'))},
      'limitations':['Retrospective official lineup, announcement and actual pregame receipt unknown.','Timeline is raw post-match evidence, not pregame feature input.','No model fitting, betting, ROI, or operating changes.']}
    mb=(json.dumps(cov,ensure_ascii=False,indent=2)+'\n').encode('utf8');save_once(snapshot/'manifest.json',mb)
    pointer={'snapshot':f'snapshots/{key}','manifest_sha256':digest(mb),'request_log_sha256':digest(request_bytes)}
    (BASE/'latest.json').write_bytes((json.dumps(pointer,indent=2)+'\n').encode('utf8'))
    print(json.dumps({k:cov[k] for k in ('scheduled','lineup_and_result_verified','starters','bench','formation_sides','substitution_events','pending_count','failed_count','request_count','snapshot_key')},ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--capture',action='store_true');parser.add_argument('--max-fixtures',type=int,default=228);args=parser.parse_args()
    if args.capture:
        plan=json.loads((BASE/'plan.json').read_text(encoding='utf8'))
        targets=[(2025,int(r['game_id'])) for r in plan['targets']]
        lock=ROOT/'capture.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            os.write(fd,str(os.getpid()).encode());os.close(fd)
            reject_unlogged_responses()
            collector.capture(targets,args.max_fixtures)
        finally:
            lock.unlink()
            build()
    else:
        build()
if __name__=='__main__':main()
