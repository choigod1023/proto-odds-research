import datetime as dt, gzip, hashlib, json, os, pathlib, sys, time, urllib.error, urllib.parse, urllib.request
P=pathlib.Path(__file__).resolve().parent
E=P/'evidence'; E.mkdir(exist_ok=True)
BASE='https://statsapi.mlb.com'

def get(name,path,params=None):
    if (P/'collector-stop.json').exists(): raise RuntimeError('Previous request failed; inspect collector-stop.json before any manual resume')
    target=E/(name+'.json.gz'); meta=E/(name+'.meta.json')
    if target.exists() and meta.exists():
        raw=gzip.decompress(target.read_bytes()); m=json.loads(meta.read_text(encoding='utf8'))
        if hashlib.sha256(raw).hexdigest()!=m['sha256']: raise ValueError('cache hash mismatch '+name)
        return json.loads(raw)
    if target.exists() or meta.exists(): raise ValueError('partial cache '+name)
    logs=list(E.glob('*.meta.json'))
    if len(logs)>=500: raise RuntimeError('request limit')
    if logs:
        last=max(dt.datetime.fromisoformat(json.loads(x.read_text(encoding='utf8'))['received_at']) for x in logs)
        time.sleep(max(0,1.1-(dt.datetime.now(dt.timezone.utc)-last).total_seconds()))
    url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
    started=dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(url,timeout=15) as r: raw=r.read(); status=r.status
    except Exception as e:
        received=dt.datetime.now(dt.timezone.utc).isoformat()
        status=getattr(e,'code',None)
        raw=e.read() if isinstance(e,urllib.error.HTTPError) else b''
        if raw: (E/(name+'.error.body.gz')).write_bytes(gzip.compress(raw,mtime=0))
        (P/'collector-stop.json').write_text(json.dumps({'url':url,'params':params or {},'requested_at':started,'received_at':received,'status':status,'error':str(e),'error_body_sha256':hashlib.sha256(raw).hexdigest() if raw else None},indent=2)+'\n',encoding='utf8',newline='\n')
        print('REQUEST_STOP',status,url,flush=True); raise
    received=dt.datetime.now(dt.timezone.utc).isoformat()
    obj=json.loads(raw)
    target.write_bytes(gzip.compress(raw,mtime=0))
    meta.write_text(json.dumps({'url':url,'params':params or {},'status':status,'requested_at':started,'received_at':received,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'availability':'retrospective_current_receipt'},sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
    print(name,status,len(raw),received,flush=True)
    return obj

def stamp(x): return dt.datetime.fromisoformat(x.replace('Z','+00:00'))
def tc(x): return x.strftime('%Y%m%d_%H%M%S')
def schedule(): return get('schedule-2026','/api/v1/schedule',{'sportId':1,'startDate':'2026-04-01','endDate':'2026-09-30','gameType':'R'})
def games(d):
    uniq={}
    for day in d['dates']:
        for g in day['games']:
            old=uniq.get(g['gamePk'])
            if old is None or (g['gameDate'],g['officialDate']) < (old['gameDate'],old['officialDate']): uniq[g['gamePk']]=g
    return sorted(uniq.values(),key=lambda g:(g['gameDate'],g['gamePk']))
def selected(gs):
    return [g for month in range(4,10) for g in [x for x in gs if stamp(x['gameDate']).year==2026 and stamp(x['gameDate']).month==month][:17]]
def feed(g,mins,tag):
    t=stamp(g['gameDate'])+dt.timedelta(minutes=mins)
    return get(f"{g['gamePk']}-{tag}",f"/api/v1.1/game/{g['gamePk']}/feed/live",{'timecode':tc(t)})
def box(g): return get(f"{g['gamePk']}-boxscore",f"/api/v1/game/{g['gamePk']}/boxscore")
def main():
    lock=P/'collector.lock'
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise SystemExit('collector lock exists; verify process is stopped before manual removal')
    try:
        os.write(fd,str(os.getpid()).encode()); os.close(fd)
        gs=games(schedule()); chosen=selected(gs); print('CENSUS',len(gs),'SELECTED',len(chosen),flush=True)
        if sys.argv[1]=='probe':
            for i in (0,34,85):
                g=chosen[i]; print('PROBE',g['gamePk'],g['gameDate'],flush=True)
                for minutes,tag in ((-180,'probe-180'),(-35,'pregame'),(120,'probe-plus120')): feed(g,minutes,tag)
                box(g)
        elif sys.argv[1]=='capture':
            for g in chosen: feed(g,-35,'pregame'); box(g)
        else: raise SystemExit('mode probe or capture')
    finally: lock.unlink()
if __name__=='__main__': main()
