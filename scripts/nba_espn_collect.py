"""Finite, resumable ESPN retrieval. Raw responses stay in local cache."""
import argparse, datetime as dt, hashlib, json, os, pathlib, time
import requests
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
CACHE = pathlib.Path('.local-test/nba-espn-history-20261108')
class StopCollection(Exception): pass
class Collector:
 def __init__(self, cache=CACHE, session=None, interval=.65):
  self.cache=pathlib.Path(cache); self.session=session or requests.Session(); self.interval=interval; self.last=0
 def get(self,kind,key):
  path=self.cache/kind/(key+'.json'); meta=path.with_suffix('.receipt.json')
  unavailable=path.with_suffix('.unavailable.body'); unavailable_meta=path.with_suffix('.unavailable.receipt.json')
  if unavailable.exists() or unavailable_meta.exists():
   try:
    raw=unavailable.read_bytes(); receipt=json.loads(unavailable_meta.read_text(encoding='utf8'))
    if receipt['status']!=404 or hashlib.sha256(raw).hexdigest()!=receipt['sha256']:raise ValueError('unavailable receipt mismatch')
    return None
   except (OSError,ValueError,KeyError,TypeError) as e:
    raise StopCollection(f'cache integrity error for unavailable {kind}/{key}; original preserved') from e
  if path.exists() or meta.exists():
   try:
    raw=path.read_bytes(); receipt=json.loads(meta.read_text(encoding='utf8'))
    if hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or receipt['status']!=200:raise ValueError('receipt mismatch')
    return json.loads(raw)
   except (OSError,ValueError,KeyError,TypeError) as e:
    self.cache.mkdir(parents=True,exist_ok=True)
    (self.cache/'FAILURE.json').write_text(json.dumps({'kind':kind,'key':key,'error':'cache integrity: '+str(e),'at':dt.datetime.now(dt.timezone.utc).isoformat()},indent=2),encoding='utf8')
    raise StopCollection(f'cache integrity error for {kind}/{key}; original preserved') from e
  path.parent.mkdir(parents=True,exist_ok=True)
  params={'dates':key,'limit':100} if kind=='scoreboard' else {'event':key}
  for attempt in range(3):
   time.sleep(max(0,self.interval-(time.monotonic()-self.last))); self.last=time.monotonic()
   try: r=self.session.get(BASE+kind,params=params,timeout=30)
   except requests.RequestException as e:
    if attempt==2:
     (self.cache/'FAILURE.json').write_text(json.dumps({'kind':kind,'key':key,'error':str(e),'attempts':3,'at':dt.datetime.now(dt.timezone.utc).isoformat()},indent=2),encoding='utf8')
     raise StopCollection(str(e)) from e
    time.sleep(2**attempt); continue
   receipt={'url':r.url,'status':r.status_code,'received_at':dt.datetime.now(dt.timezone.utc).isoformat(),'sha256':hashlib.sha256(r.content).hexdigest()}
   if r.status_code in (403,429):
    receipt['backoff']='indefinite; no automatic retry'
    (self.cache/'STOP.json').write_text(json.dumps(receipt,indent=2),encoding='utf8'); raise StopCollection(f'HTTP {r.status_code}; stopped without bypass')
   if kind=='summary' and r.status_code==404:
    unavailable.write_bytes(r.content);unavailable_meta.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print('summary unavailable',key,'HTTP 404',flush=True);return None
   if r.status_code>=500 and attempt<2: time.sleep(2**attempt); continue
   if r.status_code!=200:
    (self.cache/'FAILURE.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    raise StopCollection(f'HTTP {r.status_code}: {r.url}')
   obj=r.json(); tmp=path.with_suffix('.tmp'); tmp.write_bytes(r.content); os.replace(tmp,path)
   meta.write_text(json.dumps(receipt,indent=2),encoding='utf8'); return obj
  raise StopCollection('retry exhausted')
def main():
 p=argparse.ArgumentParser(); p.add_argument('--phase',choices=['scoreboard','summary'],required=True); args=p.parse_args(); c=Collector()
 if (c.cache/'STOP.json').exists():
  raise SystemExit('Previous HTTP 403/429 stop recorded; do not retry automatically')
 try:
  if args.phase=='scoreboard':
   days=set()
   for seed in ('20250101','20260101','20261025'):
    j=c.get('scoreboard',seed)
    for league in j.get('leagues',[]):
     days.update(x[:10].replace('-','') for x in league.get('calendar',[]) if isinstance(x,str) and '2024-12-31'<=x[:10]<='2026-10-27')
   print('calendar dates',len(days),flush=True)
   for i,day in enumerate(sorted(days),1):
    c.get('scoreboard',day)
    if i%25==0: print('scoreboards',i,'/',len(days),flush=True)
  else:
   events={}
   for path in (c.cache/'scoreboard').glob('*.json'):
    if path.name.endswith('.receipt.json'): continue
    for e in json.loads(path.read_bytes()).get('events',[]):
     if e.get('status',{}).get('type',{}).get('completed'): events[e['id']]=e.get('date','')
   print('completed events',len(events),flush=True)
   for i,(event,_) in enumerate(sorted(events.items(),key=lambda x:x[1]),1):
    c.get('summary',event)
    if i%25==0: print('summaries',i,'/',len(events),flush=True)
 except StopCollection as e: print('STOP',e,flush=True); raise SystemExit(2)
if __name__=='__main__': main()
