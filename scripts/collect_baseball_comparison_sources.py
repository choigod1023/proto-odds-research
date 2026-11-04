"""Bounded, resumable KBO schedule/lineup collector; raw HTTP bodies stay outside Git."""
import argparse, gzip, hashlib, importlib.util, json, re, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('kbo_probe',ROOT/'experiments/kbo-m0-followup-20261103/sources/probe.py')
probe=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
SAFE=re.compile(r'^[A-Za-z0-9_-]{1,80}$')
def sha(b): return hashlib.sha256(b).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def fetch(evidence,name,url,params,session=None):
    if not SAFE.fullmatch(name): raise ValueError('unsafe cache key')
    evidence.mkdir(parents=True,exist_ok=True); meta=evidence/(name+'.json')
    if meta.exists():
        rec=json.loads(meta.read_text(encoding='utf8'))
        if rec['url']!=url or rec['params']!=params: raise ValueError('cache request mismatch')
        if rec['http_status']!=200: raise ValueError('cached HTTP '+str(rec['http_status']))
        filename=rec['body_file']
        if not re.fullmatch(r'[0-9a-f]{64}\.body\.gz',filename): raise ValueError('unsafe body filename')
        raw=gzip.decompress((evidence/filename).read_bytes())
        if sha(raw)!=rec['body_sha256']: raise ValueError('cache hash mismatch')
        return raw,rec
    sess=session or requests.Session(); requested=now()
    try:
        response=sess.post(url,data=params,timeout=(5,15),allow_redirects=False,headers={'User-Agent':'Mozilla/5.0','Referer':probe.BASE+'/Schedule/GameCenter/Main.aspx'})
        raw=response.content; received=now(); digest=sha(raw); filename=digest+'.body.gz'; path=evidence/filename
        if not path.exists(): path.write_bytes(gzip.compress(raw,mtime=0))
        rec=dict(name=name,url=url,params=params,requested_at=requested,received_at=received,timestamp_basis='http_response_body_completed',http_status=response.status_code,body_sha256=digest,body_file=filename)
    except requests.RequestException as exc:
        rec=dict(name=name,url=url,params=params,requested_at=requested,failed_at=now(),http_status=None,error_type=type(exc).__name__)
        (evidence/(name+'-error-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf8'); raise
    if response.status_code!=200:
        (evidence/(name+'-error-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        raise ValueError('HTTP '+str(response.status_code))
    meta.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return raw,rec

def records(schedule_raw,schedule_meta,lineup_raw=None,lineup_meta=None,game_id=None):
    data=json.loads(schedule_raw)
    if str(data.get('code'))!='100' or not isinstance(data.get('game'),list): raise ValueError('invalid schedule')
    out=[]
    for g in data['game']:
        if game_id is not None and str(g.get('G_ID'))!=game_id: continue
        start=datetime.strptime(str(g['G_DT'])+' '+str(g['G_TM']),'%Y%m%d %H:%M').replace(tzinfo=probe.KST)
        base=dict(sport='bs',league='KBO',kickoff=start.isoformat(),home=str(g['HOME_NM']).strip(),away=str(g['AWAY_NM']).strip())
        def make(kind,meta,payload):
            identity={'kind':kind,'source_fixture_id':str(g['G_ID']),'source_url':meta['url'],'body_sha256':meta['body_sha256'],'received_at':meta['received_at']}
            rid=sha(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8'))
            return dict(record_id=kind+':'+rid,kind=kind,**base,source_url=meta['url'],body_sha256=meta['body_sha256'],received_at=meta['received_at'],timestamp_basis=meta.get('timestamp_basis','unknown'),timing_basis='actual_receipt',payload={'source_fixture_id':str(g['G_ID']),**payload})
        if lineup_raw is None:
            out.append(make('fixture',schedule_meta,{'source_state':g.get('GAME_STATE_SC'),'source_cancel':g.get('CANCEL_SC_ID'),'starters':probe.starter_metadata(g)}))
        else:
            parsed=probe.parse_lineup(lineup_raw,g);parsed['pregame_receipt']=datetime.fromisoformat(lineup_meta['received_at'])<start
            parsed['schedule_pregame_receipt']=datetime.fromisoformat(schedule_meta['received_at'])<start
            out.append(make('lineup',lineup_meta,parsed))
    if game_id is not None and len(out)!=1: raise ValueError('missing or duplicate game ID')
    return out

def save(path,obj):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8');tmp.replace(path)
def run(start,end,max_requests,evidence,output,cursor,capture_id=None,refresh=False,session=None):
    if not 1<=max_requests<=60: raise ValueError('max requests must be 1..60')
    if end<start: raise ValueError('end before start')
    evidence.mkdir(parents=True,exist_ok=True);output.parent.mkdir(parents=True,exist_ok=True);cursor.parent.mkdir(parents=True,exist_ok=True)
    state=json.loads(cursor.read_text(encoding='utf8')) if cursor.exists() else None
    if state and state['status']!='complete':
        if (state['start'],state['end'])!=(start.isoformat(),end.isoformat()): raise ValueError('cursor range mismatch')
        if capture_id and capture_id!=state['capture_id']: raise ValueError('cursor capture mismatch')
    else:
        if state and not refresh and not capture_id: return {'status':'complete','new_requests':0,'new_records':0,'next_cursor':None}
        ident=capture_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if not SAFE.fullmatch(ident): raise ValueError('unsafe capture id')
        if state and ident==state['capture_id']: raise ValueError('capture id already completed')
        state={'start':start.isoformat(),'end':end.isoformat(),'date':start.isoformat(),'game_index':-1,'capture_id':ident,'status':'partial','days_processed':0,'empty_days_total':0};save(cursor,state)
    existing=set()
    if output.exists():
        with output.open(encoding='utf8') as f:
            for line in f:
                if line.strip():existing.add(json.loads(line)['record_id'])
    new_requests=0;new_records=0;days=0;last=0.0;empty_days=0
    def get(name,url,params):
        nonlocal new_requests,last
        cached=(evidence/(name+'.json')).exists()
        if not cached:
            if new_requests>=max_requests: raise StopIteration
            time.sleep(max(0,1.5-(time.monotonic()-last)));new_requests+=1;last=time.monotonic()
        return fetch(evidence,name,url,params,session)
    def emit(rows):
        nonlocal new_records
        with output.open('a',encoding='utf8') as f:
            for row in rows:
                if row['record_id'] not in existing:
                    f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n');f.flush();existing.add(row['record_id']);new_records+=1
    try:
        while date.fromisoformat(state['date'])<=end and days<31:
            d=date.fromisoformat(state['date']);day=d.strftime('%Y%m%d')
            historical=d<datetime.now(probe.KST).date();suffix='archive' if historical else state['capture_id']
            sr,sm=get('schedule-'+day+'-'+suffix,probe.SCHEDULE,{'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day})
            obj=json.loads(sr);games=obj.get('game')
            if str(obj.get('code'))!='100' or not isinstance(games,list): raise ValueError('invalid schedule')
            ids=[str(g.get('G_ID','')) for g in games]
            if any(not re.fullmatch(r'[A-Za-z0-9]{1,40}',i) for i in ids) or len(ids)!=len(set(ids)) or any(str(g.get('G_DT'))!=day for g in games): raise ValueError('invalid official game identity')
            if state['game_index']==-1:
                emit(records(sr,sm));state['game_index']=0;save(cursor,state)
            while state['game_index']<len(games):
                g=games[state['game_index']];gid=str(g['G_ID']);lineup_suffix='archive' if historical and str(g.get('GAME_STATE_SC'))=='3' and str(g.get('GAME_RESULT_CK'))=='1' else state['capture_id'];params={'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':gid}
                lr,lm=get('lineup-'+gid+'-'+lineup_suffix,probe.LINEUP,params)
                emit(records(sr,sm,lr,lm,gid));state['game_index']+=1;save(cursor,state)
            if not games:
                empty_days+=1;state['empty_days_total']=state.get('empty_days_total',0)+1
            state['date']=(d+timedelta(days=1)).isoformat();state['game_index']=-1;state['days_processed']+=1;days+=1;save(cursor,state)
    except StopIteration: pass
    except (requests.RequestException,ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError) as exc:
        state['last_error']=str(exc)[:200];save(cursor,state)
        return {'status':'error','error':state['last_error'],'new_requests':new_requests,'new_records':new_records,'next_cursor':state['date']}
    if date.fromisoformat(state['date'])>end:state['status']='complete';state.pop('last_error',None);save(cursor,state)
    return {'status':state['status'],'new_requests':new_requests,'new_records':new_records,'empty_days':empty_days,'batch_days':days,'next_cursor':None if state['status']=='complete' else state['date'],'capture_id':state['capture_id'],'empty_days_total':state.get('empty_days_total',0)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--start',required=True,type=date.fromisoformat);p.add_argument('--end',required=True,type=date.fromisoformat);p.add_argument('--max-requests',type=int,default=20);p.add_argument('--evidence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--cursor',required=True,type=Path);p.add_argument('--capture-id');p.add_argument('--refresh',action='store_true');a=p.parse_args()
    result=run(a.start,a.end,a.max_requests,a.evidence,a.output,a.cursor,a.capture_id,a.refresh);print(json.dumps(result,ensure_ascii=False));return 1 if result['status']=='error' else 0
if __name__=='__main__':raise SystemExit(main())
