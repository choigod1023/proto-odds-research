import argparse, datetime as dt, hashlib, json, os, time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
E=ROOT/'evidence'
H={'Channel':'WEB','TeamCode':'XX','X-Requested-With':'XMLHttpRequest','lang':'ko'}
BASE='https://api.kbl.or.kr'
P={'fromDate':'20251001','toDate':'20260430','tcodeList':'all'}
last=0.0

def write_json(p,obj):
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def fetch(label,path,params=None):
    global last
    body=E/(label+'.body'); meta=E/(label+'.json')
    if body.exists() and meta.exists():
        m=json.loads(meta.read_text(encoding='utf-8'))
        if m['status']==200 and hashlib.sha256(body.read_bytes()).hexdigest()==m['sha256']: return body.read_bytes()
        raise RuntimeError('cached failure or hash mismatch: '+label)
    time.sleep(max(0,1.1-(time.monotonic()-last)))
    last=time.monotonic()
    r=requests.get(BASE+path,params=params,headers=H,timeout=15)
    received=dt.datetime.now(dt.timezone.utc).isoformat()
    body.write_bytes(r.content)
    write_json(meta,{'url':r.url,'params':params or {},'request_headers':H,'received_at':received,'status':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest(),'bytes':len(r.content),'availability':'retrospective_current_receipt'})
    if r.status_code != 200: raise RuntimeError(f'{label}: HTTP {r.status_code}, stop')
    return r.content

def census():
    rows=json.loads(fetch('schedule','/match/list',P))
    assert isinstance(rows,list)
    selected=sorted([r for r in rows if r.get('glkey')=='S47G01'],key=lambda x:(x['gameDate'],x['gameStart'],x['gmkey']))
    assert len({r['gmkey'] for r in selected})==len(selected)
    assert all(r['seasonCode']==47 and r['gameCode']=='01' and r['seasonName1']=='2025-2026' and r['tcodeH']!=r['tcodeA'] for r in selected)
    keys=('gmkey','glkey','gameDate','gameStart','tcodeH','tcodeA','tnameH','tnameA','seasonCode','gameCode','seasonName1')
    safe=[{k:r.get(k) for k in keys} for r in selected]
    ids=[x['gmkey'] for x in safe] if len(safe)<120 else [safe[i*(len(safe)-1)//119]['gmkey'] for i in range(120)]
    assert len(ids)==len(set(ids))
    write_json(ROOT/'census.json',{'all_response_rows':len(rows),'regular_season_fixtures':safe,'other_rows':len(rows)-len(safe),'sample_ids':ids,'selection':'chronological floor(i*(N-1)/119), i=0..119; no outcome filter','strict_pit_receipts':0})
    print('census',len(rows),'regular',len(safe),'sample',len(ids),flush=True)

def boxes():
    data=json.loads((ROOT/'census.json').read_text(encoding='utf-8'))
    for i,g in enumerate(data['sample_ids'],1):
        for kind in ('header','player-stat'):
            path='/match/'+g+('' if kind=='header' else '/player-stat')
            b=fetch('postgame-'+g+'-'+kind,path)
            obj=json.loads(b)
            if not obj: raise RuntimeError('empty '+g+' '+kind)
        print(i,g,flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('mode',choices=['census','boxes']); args=ap.parse_args()
    E.mkdir(exist_ok=True)
    lock=ROOT/'.collector.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode()); os.close(fd)
        census() if args.mode=='census' else boxes()
    finally: lock.unlink(missing_ok=True)
