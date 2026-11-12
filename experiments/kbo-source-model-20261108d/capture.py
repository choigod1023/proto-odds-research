"""Bounded public-source probe with retained bytes; no scheduler or production writes."""
import argparse,hashlib,json,time,urllib.robotparser
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
import requests

URLS=('https://mykbostats.com/robots.txt','https://mykbostats.com/players/1990','https://mykbostats.com/games/14037','https://mykbostats.com/players/1990?year=2025','https://www.koreabaseball.com/robots.txt')
AGENT='KBOResearchSourceProbe/1.0 (+manual-research; contact site operator for reuse)'
def now():return datetime.now(timezone.utc).isoformat()
def fetch(session,url):
    started=now();response=session.get(url,timeout=(6,12),allow_redirects=False,stream=True,headers={'User-Agent':AGENT}); body=b''
    for chunk in response.iter_content(65536):
        body+=chunk
        if len(body)>2_000_000:raise ValueError('response_too_large')
    end=now();return body,{'url':url,'requested_at':started,'received_at':end,'timestamp_basis':'http_body_completed_local_clock','status':response.status_code,'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'content_type':response.headers.get('Content-Type')}
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=False);session=requests.Session();receipts=[]
    body,meta=fetch(session,URLS[0]);(out/'mykbo-robots.bin').write_bytes(body);receipts.append(meta)
    parser=urllib.robotparser.RobotFileParser();parser.set_url(URLS[0]);parser.parse(body.decode('utf8','replace').splitlines());allowed=meta['status']==200 and all(parser.can_fetch(AGENT,u) for u in URLS[1:4]);delay=parser.crawl_delay(AGENT) or 5
    if allowed:
        for name,url in [('player.bin',URLS[1]),('game.bin',URLS[2]),('player-2025.bin',URLS[3])]:
            time.sleep(max(5,delay));body,meta=fetch(session,url);(out/name).write_bytes(body);receipts.append(meta)
            if meta['status'] in (403,429) or meta['status']!=200:break
    body,meta=fetch(session,URLS[4]);(out/'kbo-robots.bin').write_bytes(body);receipts.append(meta)
    (out/'receipts.json').write_text(json.dumps({'receipts':receipts,'mykbo_robots_allowed':allowed,'delay_seconds':delay},ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'receipts':receipts,'mykbo_robots_allowed':allowed},ensure_ascii=False))
if __name__=='__main__':main()
