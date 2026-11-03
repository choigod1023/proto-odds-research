"""Small, cached official match-centre capture. Stops on access denial or errors."""
import argparse, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
SAMPLES = [(2024,2,1),(2025,2,1),(2025,2,253),(2026,2,1),(2025,1,1)]
BASE = 'https://www.kleague.com'

def fetch(session, year, league, game, kind):
    stem=f'{year}-{league}-{game}-{kind}'
    data_path=ROOT/'raw'/f'{stem}.bin'
    meta_path=ROOT/'raw'/f'{stem}.json'
    if data_path.exists() or meta_path.exists():
        if not data_path.exists() or not meta_path.exists(): raise RuntimeError(f'incomplete cache: {stem}')
        meta=json.loads(meta_path.read_text(encoding='utf8'))
        if hashlib.sha256(data_path.read_bytes()).hexdigest()!=meta['sha256']: raise RuntimeError(f'cache hash mismatch: {stem}')
        return meta
    params={'year':str(year),'meetSeq':str(league),'gameId':str(game)}
    if kind=='match':
        params['leagueId']=str(league)
        url=BASE+'/match.do'; method='GET'
        r=session.get(url,params=params,timeout=15,allow_redirects=False,stream=True)
    else:
        url=BASE+'/api/ddf/match/matchInfo.do'; method='POST'
        r=session.post(url,data=params,timeout=15,allow_redirects=False,stream=True)
    received_status=r.status_code
    if received_status in (403,429) or received_status!=200:
        r.close(); raise RuntimeError(f'stopped HTTP {received_status} {stem}')
    chunks=[]; size=0
    for chunk in r.iter_content(65536):
        size+=len(chunk)
        if size>3_000_000: r.close(); raise RuntimeError('response exceeds 3 MB')
        chunks.append(chunk)
    body=b''.join(chunks); received=datetime.now(timezone.utc).isoformat(); r.close()
    meta={'method':method,'url':url,'params':params,'status':received_status,'received_at':received,'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'content_type':r.headers.get('Content-Type'),'receipt_independently_verified':False}
    data_path.write_bytes(body); meta_path.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf8')
    time.sleep(1)
    return meta

def main():
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');args=p.parse_args()
    if not args.collect: p.error('pass --collect to request fixed small sample; existing cache is reused')
    (ROOT/'raw').mkdir(exist_ok=True)
    with requests.Session() as s:
        s.headers.update({'User-Agent':'K2-lineup-research/1.0','Referer':BASE+'/'})
        for y,l,g in SAMPLES:
            for kind in ('match','info'):
                print(json.dumps(fetch(s,y,l,g,kind)),flush=True)
if __name__=='__main__': main()
