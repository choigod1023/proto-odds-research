"""Bounded read-only round fetch; previous KBO responses are immutable cache."""
import gzip
import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
KST=timezone(timedelta(hours=9))
BASE='https://www.betman.co.kr'
MAX_NEW_REQUESTS=250

def sha(raw): return hashlib.sha256(raw).hexdigest()
def readgz(path): return gzip.decompress(path.read_bytes())

def load_fixtures():
    rows={}
    sources=json.loads((ROOT/'inputs/sources.json').read_text(encoding='utf8'))
    for source in sources:
        raw=readgz(ROOT/source['file'])
        if sha(raw)!=source['sha256_raw']: raise ValueError('input hash mismatch')
        data=json.loads(raw)
        matches=data['fixtures'] if Path(source['file']).name.startswith('k2-') else data['data']['scheduleList']
        for row in matches:
            if row.get('year') not in (2025,2026) or row.get('leagueId') not in (1,2): continue
            key=(row['year'],row['leagueId'],row['gameId'])
            if key in rows and rows[key]!=row: raise ValueError('conflicting official schedule '+str(key))
            rows[key]=row
    return rows

def select_rounds(fixtures,rounds):
    mapping={};plan={}
    for key,f in fixtures.items():
        if f.get('gameStatus')!='FE' or f.get('endYn')!='Y': continue
        day=date.fromisoformat(f['gameDate'].replace('.','-'))
        valid=[x for x in rounds if x.get('gmOsidTsYear')==f['year'] and x.get('saleStartDate') and x.get('saleEndDate')
               and datetime.fromtimestamp(x['saleStartDate']/1000,KST).date()<=day
               <=datetime.fromtimestamp(x['saleEndDate']/1000,KST).date()]
        mapping[key]=[x['gmTs'] for x in valid]
        for x in valid: plan[x['gmTs']]=x
    return mapping,plan

def main():
    rows=load_fixtures(); records_path=ROOT/'evidence/attempts.json'
    records=json.loads(records_path.read_text(encoding='utf8'))
    if any(x.get('http_status') in (403,429) for x in records): raise RuntimeError('Previous 403/429: stop')
    closed=next(x for x in records if x['name']=='closed-list' and x['http_status']==200)
    raw=readgz(ROOT/closed['body_file']);assert sha(raw)==closed['body_sha256']
    mapping,plan=select_rounds(rows,json.loads(raw)['schedules']['data'])
    print('planned',len(plan),'fixtures',len(mapping),flush=True)
    present={x['name']:x for x in records if x.get('http_status')==200}
    tasks=[('football-slip-page','GET',BASE+'/main/mainPage/gamebuy/gameSlip.do?gmId=G101&gmTs=260040',None),
           ('official-slip-js','GET','https://cdn.betman.co.kr/js/slip/slipUtils.js',None)]
    for ts,item in sorted(plan.items()):
        y,rd=item['gmOsidTsYear'],item['gmOsidTs'];name=f'detail-{y}-{rd}'
        tasks.append((name,'POST',BASE+'/buyPsblGame/gameInfoInq.do',{'gmId':'G101','gmTs':str(ts),'gameYear':str(y),'_sbmInfo':{'debugMode':'false'}}))
    new_tasks=[task for task in tasks if task[0] not in present]
    prior_new=sum(1 for x in records if x.get('origin')=='kleague_new_request')
    if prior_new+len(new_tasks)>MAX_NEW_REQUESTS: raise RuntimeError('new request budget exceeded')
    with requests.Session() as session:
        for name,method,url,payload in new_tasks:
            record={'name':name,'origin':'kleague_new_request','method':method,'url':url,'request_json':payload,'timeout_seconds':15}
            try:
                response=session.request(method,url,json=payload,timeout=15,headers={'User-Agent':'Mozilla/5.0','X-Requested-With':'XMLHttpRequest','Referer':BASE+'/main/mainPage/gamebuy/closedGameList.do'})
                raw=response.content; record.update(received_at=datetime.now(timezone.utc).isoformat(),http_status=response.status_code,content_type=response.headers.get('Content-Type'),body_sha256=sha(raw),body_file=f'evidence/{name}.body.gz',body_encoding='gzip; sha256 is of decompressed response bytes')
                (ROOT/record['body_file']).write_bytes(gzip.compress(raw,mtime=0))
                records.append(record); records_path.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                print(name,response.status_code,flush=True)
                if response.status_code in (403,429): break
            except requests.RequestException as e:
                record.update(failed_at=datetime.now(timezone.utc).isoformat(),http_status=None,error_type=type(e).__name__,error=str(e))
                records.append(record);records_path.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(name,'failed',flush=True)
            time.sleep(1)
    print('finished',flush=True)

if __name__=='__main__': main()
