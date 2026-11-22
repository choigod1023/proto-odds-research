import argparse, datetime, gzip, hashlib, json, os, re, time
from pathlib import Path
from urllib.parse import urlencode, urlparse
import requests

ROOT=Path(__file__).resolve().parent
DIR=ROOT/'provider'; RAW=DIR/'raw'; LEDGER=DIR/'requests.json'; LOCK=DIR/'collector.lock'
SCORE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
SUMMARY='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary'
DATES=['2025-10-%02d'%d for d in (22,25,28,31)]+['2025-%02d-%02d'%(m,d) for m in (11,12) for d in (1,8,15,22)]+['2026-%02d-%02d'%(m,d) for m in (1,2,3) for d in (1,8,15,22)]+['2026-04-%02d'%d for d in (1,8,12)]

def sha(b): return hashlib.sha256(b).hexdigest()
def put(path, obj):
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n'); os.replace(tmp,path)
def decode_record(row):
    packed=(DIR/row['file']).read_bytes()
    if sha(packed)!=row['stored_sha256']: raise ValueError('stored hash mismatch')
    data=gzip.decompress(packed)
    if sha(data)!=row['body_sha256']: raise ValueError('body hash mismatch')
    return data

class Fetcher:
    def __init__(self):
        self.rows=json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else []
        self.last={};self.session=requests.Session();self.session.headers['User-Agent']='Mozilla/5.0';self.blocked={urlparse(r['url']).netloc for r in self.rows if r.get('status') in (403,429)}
    def get(self, name, url, params):
        old=next((x for x in self.rows if x['name']==name),None)
        if old:
            if old.get('status')==200:return decode_record(old)
            return None
        host=urlparse(url).netloc
        if host in self.blocked or len(self.rows)>=200:return None
        wait=1.1-(time.monotonic()-self.last.get(host,0));
        if wait>0:time.sleep(wait)
        row={'name':name,'url':url,'params':params,'availability':'current_retrieval_only'}
        try:
            response=self.session.get(url,params=params,timeout=15); self.last[host]=time.monotonic();row['received_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();row['status']=response.status_code; body=response.content
            packed=gzip.compress(body,mtime=0); filename='raw/'+name+'.body.gz'; (DIR/filename).write_bytes(packed)
            row.update(file=filename,body_sha256=sha(body),stored_sha256=sha(packed),bytes=len(body))
            if response.status_code in (403,429):self.blocked.add(host)
        except requests.RequestException as e:
            self.last[host]=time.monotonic();row.update(received_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=None,error=type(e).__name__);body=None
        self.rows.append(row);put(LEDGER,self.rows)
        print(name,row['status'],row.get('bytes',0),flush=True)
        return body if row['status']==200 else None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');args=parser.parse_args()
    DIR.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
    fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.write(fd,str(os.getpid()).encode());os.close(fd)
    try:
        fetch=Fetcher()
        # A separate official team page is checked once; never re-request a blocked host.
        fetch.get('official-team-schedule','https://www.nba.com/nuggets/schedule',{})
        dates=DATES[:1] if args.probe else DATES
        for date in dates:
            fetch.get('scoreboard-'+date,SCORE,{'dates':date.replace('-',''),'limit':100})
        if args.probe:return
        # Selection is withheld until its parser has validated every date in a month.
        import nba_provider_parse as parse
        census=parse.build_census(fetch.rows,DIR,DATES)
        put(DIR/'census.json',census)
        for ev in census['selected']:
            fetch.get('summary-'+ev['provider_game_id'],SUMMARY,{'event':ev['provider_game_id']})
        # Three independently chosen official discovery games; not part of the selected cohort.
        for gid in ('401810129','401810440','401811052'):
            fetch.get('summary-'+gid,SUMMARY,{'event':gid})
    finally:
        LOCK.unlink()

if __name__=='__main__':main()
