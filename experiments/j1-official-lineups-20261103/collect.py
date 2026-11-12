from __future__ import annotations
import calendar, datetime as dt, gzip, hashlib, json, os, re, time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
E=ROOT/'evidence'; E.mkdir(exist_ok=True)
LOG=E/'requests.jsonl'; LOCK=ROOT/'collector.lock'
BASE='https://www.jleague.jp'
MIN_INTERVAL=1.2
STOP=ROOT/'collector.stop'
MAX_LOGGED_REQUESTS=480  # leaves room for the prior exploratory requests

def get(kind, key, url, params=None):
    cache=E/f'{kind}-{key}.html.gz'
    if cache.exists():
        records=[json.loads(x) for x in LOG.read_text(encoding='utf-8').splitlines()]
        record=next((x for x in reversed(records) if x.get('kind')==kind and x.get('key')==key and x.get('status')==200),None)
        raw=gzip.decompress(cache.read_bytes())
        if not record or hashlib.sha256(raw).hexdigest()!=record['body_sha256']:
            raise ValueError('cache provenance mismatch: '+str(cache))
        return raw
    if STOP.exists(): raise RuntimeError('previous 403/429: do not resume automatically')
    if LOG.exists() and len(LOG.read_text(encoding='utf-8').splitlines())>=MAX_LOGGED_REQUESTS:
        raise RuntimeError('request budget exhausted')
    time.sleep(MIN_INTERVAL)
    try:
        response=requests.get(url,params=params,timeout=15)
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        raw=response.content
        rec={'kind':kind,'key':key,'request_url':response.request.url,'final_url':response.url,'params':params or {},'status':response.status_code,'received_at_utc':now,'body_sha256':hashlib.sha256(raw).hexdigest()}
        if response.ok:
            cache.write_bytes(gzip.compress(raw,mtime=0));rec['body_file']=cache.name
        with LOG.open('a',encoding='utf-8',newline='\n') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        print(kind,key,response.status_code,len(raw),flush=True)
        if response.status_code in (403,429):
            STOP.write_text(str(response.status_code)+'\n',encoding='utf-8')
            raise RuntimeError('blocked: stop all requests')
        if not response.ok: raise RuntimeError(f'HTTP {response.status_code}')
        return raw
    except requests.RequestException as exc:
        rec={'kind':kind,'key':key,'url':url,'params':params or {},'received_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'error':str(exc)}
        with LOG.open('a',encoding='utf-8',newline='\n') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        raise

def main():
    try: fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise SystemExit('collector lock exists; inspect owner before resuming')
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    try:
        ids=set()
        for month in range(2,13):
            start=f'2025-{month:02}-01'; end=f'2025-{month:02}-{calendar.monthrange(2025,month)[1]:02}'
            raw=get('census',f'2025-{month:02}',BASE+'/j1/match/search-list/',{'category':'j1','startdate':start,'enddate':end,'period':'custom'})
            ids.update(re.findall(rb'/match/j1/2025/(\d{6})/',raw))
        ordered=sorted(x.decode() for x in ids)
        if not ordered: raise RuntimeError('empty census: no matches; do not proceed')
        selected=[ordered[(i*(len(ordered)-1))//99] for i in range(100)] if len(ordered)>=100 else ordered
        (ROOT/'selection.json').write_text(json.dumps({'official_fixture_ids':ordered,'selected_ids':selected,'selection_rule':'100 equally spaced ranks floor(i*(N-1)/99)'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        print('census',len(ordered),'selected',len(selected),flush=True)
        for code in selected:get('lineup',code,f'{BASE}/match/j1/2025/{code}/lineup/')
    finally:
        LOCK.unlink(missing_ok=True)
if __name__=='__main__':main()
