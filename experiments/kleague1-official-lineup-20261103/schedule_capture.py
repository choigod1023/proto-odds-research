"""Collect current official monthly schedule in a bounded, resumable batch."""
import datetime as dt
import hashlib
import json
import time
from pathlib import Path
import requests

BASE=Path(__file__).resolve().parent
LOG=BASE/'schedule-requests.json'
URL='https://www.kleague.com/getScheduleList.do'

def main():
    entries=json.loads(LOG.read_text(encoding='utf8')) if LOG.exists() else []
    if any(x.get('status') in (401,403,429) for x in entries):raise ValueError('access restricted: stop')
    for year in (2026,2025,2024,2023):
        if year != 2026: continue  # later seasons remain explicitly pending
        for month in range(1,13):
            key=f'{year}-{month:02d}';name=f'raw/schedule-{key}.json'
            old=[e for e in entries if e.get('key')==key]
            if old:
                e=old[-1]
                if e.get('status')!=200 or e.get('result_code')!='200':raise ValueError('previous unsuccessful request: stop')
                if hashlib.sha256((BASE/name).read_bytes()).hexdigest()!=e['sha256']:raise ValueError('cached schedule hash mismatch')
                continue
            params={'leagueId':1,'teamId':'','year':str(year),'month':f'{month:02d}','ticketYn':''}
            e={'key':key,'method':'POST','url':URL,'params':params,'requested_at':dt.datetime.now(dt.timezone.utc).isoformat(),'timeout_seconds':15,'max_bytes':3000000}
            try:
                with requests.post(URL,json=params,timeout=15,allow_redirects=False,stream=True) as r:
                    e['status']=r.status_code;chunks=[];size=0
                    for chunk in r.iter_content(65536):
                        size+=len(chunk)
                        if size>3000000:raise ValueError('response_too_large')
                        chunks.append(chunk)
                    b=b''.join(chunks);e.update(received_at=dt.datetime.now(dt.timezone.utc).isoformat(),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),raw_file=name)
                    (BASE/name).write_bytes(b);data=json.loads(b);e['result_code']=str(data.get('resultCode'));e['rows']=len(data['data']['scheduleList'])
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                e.update(error=type(exc).__name__,ended_at=dt.datetime.now(dt.timezone.utc).isoformat())
            entries.append(e);LOG.write_bytes((json.dumps(entries,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
            print(key,e.get('status'),e.get('rows'),e.get('error',''),flush=True)
            if e.get('status')!=200 or e.get('result_code')!='200' or e.get('error'):raise RuntimeError('request failed: stop without retry')
            time.sleep(1)
if __name__=='__main__':
    import os
    lock=BASE/'capture.lock'; fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd);main()
    finally:lock.unlink()
