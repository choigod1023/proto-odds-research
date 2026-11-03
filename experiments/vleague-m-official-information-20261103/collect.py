"""Bounded public KOVO detail capture; resume without overwriting verified bytes."""
import datetime, hashlib, json, os, sys, time
from pathlib import Path
import requests
P=Path(__file__).resolve().parent
E=P/'evidence'
LOCK=P/'collector.lock'
HEADERS={'X-Service-Name':'user','Accept-Language':'ko'}
PARAMS={'gcode':'001','seasonCode':'022','leagueCode':'201'}
def digest(b): return hashlib.sha256(b).hexdigest()
def main():
 try: fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 except FileExistsError: raise SystemExit('collector lock exists; inspect its owner before manual recovery')
 try:
  with os.fdopen(fd,'w') as f: f.write(str(os.getpid()))
  schedule=json.loads((E/'schedule.body').read_bytes())['payload']; games=sorted(schedule['content'],key=lambda x:(x['gdate'],x['gnum']))
  assert schedule['page']['totalElements']==len(games)==126
  assert all(x['seasonCode']=='022' and x['leagueCode']=='201' and x['gender']=='1' for x in games)
  assert len({x['gnum'] for x in games})==len(games)
  session=requests.Session(); last=0; done=0
  for x in games:
   n=x['gnum']; name=f'match-{n:03}'; body=E/(name+'.body'); meta=E/(name+'.meta.json')
   if body.exists() or meta.exists():
    if not body.exists() or not meta.exists(): raise RuntimeError(f'incomplete cached {name}')
    b=body.read_bytes(); m=json.loads(meta.read_text(encoding='utf8'))
    if digest(b)!=m['sha256'] or m['status']!=200: raise RuntimeError(f'bad cached {name}')
   else:
    time.sleep(max(0,1.1-(time.monotonic()-last)))
    r=session.get(f'https://user-api.kovo.co.kr/stat/game-schedule/{n}',params=PARAMS,headers=HEADERS,timeout=15); last=time.monotonic()
    received=datetime.datetime.now(datetime.timezone.utc).isoformat(); b=r.content
    m={'url':r.url,'parameters':PARAMS,'received_at':received,'status':r.status_code,'sha256':digest(b)}
    body.write_bytes(b);meta.write_text(json.dumps(m,indent=2)+'\n',encoding='utf8',newline='\n')
    if r.status_code in (403,429): raise RuntimeError(f'stopped on HTTP {r.status_code}')
    r.raise_for_status()
   d=json.loads(b); g=(d.get('payload') or {}).get('game') or {}
   if d.get('result',{}).get('status')!=200 or any(g.get(k)!=x.get(k) for k in ('gnum','gdate','hcode','acode','seasonCode','leagueCode','gender')): raise RuntimeError(f'wrong or failed response {name}')
   done+=1
   if done%20==0: print(f'{done}/{len(games)}',flush=True)
  print(f'completed {done}/{len(games)}',flush=True)
 finally: LOCK.unlink()
if __name__=='__main__': main()
