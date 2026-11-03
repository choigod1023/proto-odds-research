"""Bounded public KOVO season capture. All responses are postgame, never PIT features."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import requests

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'evidence' / 'postgame'
MANIFEST = ROOT / 'capture-manifest.json'
LOCK = ROOT / '.collector.lock'
BASE = 'https://user-api.kovo.co.kr/stat/real-time'

def sha(data): return hashlib.sha256(data).hexdigest()
def load(path): return json.loads(path.read_bytes())
def save(obj):
    tmp = MANIFEST.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    os.replace(tmp,MANIFEST)

def census():
    doc=load(EVIDENCE/'schedule.httpbody')
    assert doc['result']['status']==200
    games=[g for g in doc['payload'] if g['match']=='001' and g['season']['value']=='022' and g['league']['value']=='201' and g['gender']=='FEMALE' and g.get('endTime')]
    games.sort(key=lambda g:g['number'])
    assert len(games)==126 and len({g['number'] for g in games})==126
    return games

def validate(body,g):
    d=json.loads(body); assert d['result']['status']==200 and isinstance(d.get('payload'),dict)
    p=d['payload']; x=p['game']; l=p['lineup']
    assert str(x['gameNum'])==str(g['number']) and x['seasonCode']=='022'
    assert x['homeCode']==g['homeTeam']['value'] and x['awayCode']==g['awayTeam']['value']
    assert x['gameDate']==g['gameDate'] and l and l['rset']==1 and str(l['gnum'])==str(g['number']) and l['gpart']=='201'
    return p

def retry_permitted(manifest):
    return (manifest.get('last_error') or {}).get('status') not in (403,429)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--max-new',type=int,default=126);args=ap.parse_args()
    if not 0 <= args.max_new <= 126: raise ValueError('max-new must be in 0..126')
    fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
    try:
        games=census(); existing=load(MANIFEST) if MANIFEST.exists() else {'schema':'kovo-women-capture-v1','protocol_commit':'0df0fc6','games':{}}
        if not retry_permitted(existing):
            raise RuntimeError('blocked by prior 403/429; inspect before any further request')
        discovery=load(EVIDENCE/'discovery.json')
        for event in discovery:
            if not event['name'].startswith('sample_set1_'):continue
            ident=event['name'].removeprefix('sample_set1_'); path=EVIDENCE/(event['name']+'.httpbody'); g=next(x for x in games if str(x['number'])==ident)
            body=path.read_bytes(); assert sha(body)==event['sha256'];validate(body,g)
            existing['games'].setdefault(ident,{**event,'file':str(path.relative_to(ROOT)).replace('\\','/'),'availability':'retrospective_postgame_pit_unverified'})
        save(existing)
        done=0;session=requests.Session();session.headers.update({'X-Service-Name':'user','Accept-Language':'ko'})
        for g in games:
            ident=str(g['number'])
            if ident in existing['games']:
                e=existing['games'][ident];assert sha((ROOT/e['file']).read_bytes())==e['sha256'];continue
            if done>=args.max_new:break
            params={'season':'022','gcode':'001','gnum':g['number'],'round':g['round'],'hcode':g['homeTeam']['value'],'acode':g['awayTeam']['value'],'rset':1,'page':0,'size':10000}
            time.sleep(1.1)
            try:r=session.get(BASE,params=params,timeout=15)
            except requests.RequestException as e: print('STOP network',type(e).__name__,flush=True);break
            received=dt.datetime.now(dt.timezone.utc).isoformat();body=r.content
            path=EVIDENCE/f'realtime_set1_{ident}.httpbody';path.write_bytes(body)
            event={'name':'realtime_set1_'+ident,'url':r.url,'params':params,'status':r.status_code,'received_at':received,'sha256':sha(body),'length':len(body),'file':str(path.relative_to(ROOT)).replace('\\','/'),'availability':'retrospective_postgame_pit_unverified'}
            if r.status_code != 200:
                existing['last_error']=event;save(existing);print('STOP HTTP',r.status_code,ident,flush=True);break
            try: validate(body,g)
            except (ValueError,KeyError,TypeError,AssertionError) as e:
                existing['last_error']=event;save(existing);print('STOP invalid',ident,type(e).__name__,flush=True);break
            existing['games'][ident]=event; existing.pop('last_error',None);save(existing);done+=1
            if done%20==0:print('new',done,'total',len(existing['games']),flush=True)
        print('FINISH',len(existing['games']),'/',len(games),'new',done,flush=True)
    finally:
        LOCK.unlink(missing_ok=True)
if __name__=='__main__':main()
