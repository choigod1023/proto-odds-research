"""Bounded sequential official schedule and match-centre capture, cache and resume."""
import argparse,datetime,gzip,hashlib,json,os,time
if os.name == 'nt':
 import msvcrt
else:
 import fcntl
from pathlib import Path
import requests
R=Path(__file__).resolve().parent
RAW=R/'raw'; BASE='https://www.kleague.com'; MAX_FIXTURES_PER_YEAR=400

def sha(b): return hashlib.sha256(b).hexdigest()
def write_json(p,obj): p.write_bytes((json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8'))
def lock_file(f):
 if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
 else:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
def unlock_file(f):
 if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
 else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
def read_capture(stem):
 p=RAW/(stem+'.gz'); m=RAW/(stem+'.json')
 if not p.exists() and not m.exists(): return None
 if not p.exists() or not m.exists(): raise RuntimeError('incomplete cache '+stem)
 meta=json.loads(m.read_bytes()); compressed=p.read_bytes(); body=gzip.decompress(compressed)
 if sha(compressed)!=meta['stored_sha256'] or sha(body)!=meta['source_sha256']: raise RuntimeError('cache hash mismatch '+stem)
 return body

def capture(session,stem,url,params,method):
 cached=read_capture(stem)
 if cached is not None:return cached
 r=session.request(method,url,params=params if method=='GET' else None,json=params if method=='POST' else None,timeout=(10,15),allow_redirects=False,stream=True)
 status=r.status_code
 if status!=200: r.close();raise RuntimeError(f'stop HTTP {status} for {stem}')
 chunks=[];size=0
 for chunk in r.iter_content(65536):
  size+=len(chunk)
  if size>3_000_000:r.close();raise RuntimeError('response too large '+stem)
  chunks.append(chunk)
 body=b''.join(chunks); received=datetime.datetime.now(datetime.timezone.utc).isoformat();r.close()
 comp=gzip.compress(body,compresslevel=9,mtime=0)
 meta={'method':method,'url':url,'params':params,'status':status,'received_at':received,'source_sha256':sha(body),'stored_sha256':sha(comp),'source_bytes':len(body),'stored_bytes':len(comp),'independent_receipt_verified':False}
 (RAW/(stem+'.gz')).write_bytes(comp);write_json(RAW/(stem+'.json'),meta)
 time.sleep(1)
 return body

def schedule(session,year):
 allrows=[]
 for m in range(1,13):
  params={'leagueId':'2','teamId':'','ticketStatus':'','year':str(year),'month':f'{m:02}','ticketYn':'N'}
  obj=json.loads(capture(session,f'schedule-{year}-{m:02}',BASE+'/getScheduleList.do',params,'POST'))
  if str(obj.get('resultCode'))!='200' or not isinstance(obj.get('data',{}).get('scheduleList'),list):raise RuntimeError(f'invalid schedule {year}-{m}')
  for x in obj['data']['scheduleList']:
   if x.get('year')==year and x.get('leagueId')==2 and x.get('meetSeq')==2 and str(x.get('gameDate',''))[5:7]==f'{m:02}':allrows.append(x)
 ids=[x['gameId'] for x in allrows]
 if len(ids)>MAX_FIXTURES_PER_YEAR:raise RuntimeError(f'schedule exceeds per-year cap {year}: {len(ids)}')
 if len(ids)!=len(set(ids)):raise RuntimeError(f'duplicate official game ID {year}')
 return allrows

def run(years):
 RAW.mkdir(parents=True,exist_ok=True)
 lock=(R/'collector.lock').open('a+b');lock.seek(0)
 try:lock_file(lock)
 except OSError:raise RuntimeError('another collector holds lock')
 try:
  with requests.Session() as s:
   s.headers.update({'User-Agent':'K2-lineup-cohort-research/1.0','Referer':BASE+'/'})
   for year in years:
    rows=schedule(s,year)
    # Freeze the census before looking at or collecting individual lineups.
    census={'year':year,'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'definition':'all official monthly schedule rows, leagueId=meetSeq=2; finished FE/endYn Y collected in schedule order; unplayed retained in census', 'fixtures':rows}
    census_path=R/f'census-{year}.json'
    if census_path.exists():
     old=json.loads(census_path.read_bytes())
     if old['fixtures']!=rows:raise RuntimeError('census changed; refuse silent replacement')
    else:write_json(census_path,census)
    finished=[x for x in rows if x.get('gameStatus')=='FE' and x.get('endYn')=='Y']
    print(f'YEAR {year} census={len(rows)} finished={len(finished)} other={len(rows)-len(finished)}',flush=True)
    for i,x in enumerate(finished,1):
     params={'year':str(year),'leagueId':'2','meetSeq':'2','gameId':str(x['gameId'])}
     capture(s,f'match-{year}-2-{x["gameId"]}',BASE+'/match.do',params,'GET')
     if i%20==0 or i==len(finished):print(f'YEAR {year} captured_or_cached={i}/{len(finished)}',flush=True)
 finally:
  lock.seek(0);unlock_file(lock);lock.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--years',type=int,nargs='+',choices=[2026,2025],default=[2026,2025]);a=p.parse_args()
 if a.years not in ([2026],[2025],[2026,2025]):raise ValueError('expected --years 2026 2025, 2026 or 2025')
 run(a.years)
