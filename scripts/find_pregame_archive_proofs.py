import gzip, hashlib, json, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUT = Path('experiments/matched-archive-proofs-20261104')
CASES = [
 {'sport':'sc','kind':'article','source_url':'https://golfmasterj.tistory.com/entry/3%EC%9B%947%EC%9D%BC-%ED%86%A0%EC%9A%94%EC%9D%BC-K%EB%A6%AC%EA%B7%B8%C2%B7KBL%C2%B7V-%EB%A6%AC%EA%B7%B8-%EC%8A%B9%EB%B6%80%EC%98%88%EC%B8%A1-%E2%80%93-%EC%8A%A4%EB%A7%88%ED%8A%B8%EB%B0%B8%EB%A5%98%EB%A1%9C%EC%A7%81-20-%ED%86%B5%ED%95%A9-%EB%A6%AC%ED%8F%AC%ED%8A%B8','kickoff_bound':'20260307000000','expected':['서울E','경남'],'source_record':'tipster-year korean candidates first row'},
 {'sport':'bs','kind':'article','source_url':'https://www.tonyspicks.com/2025/10/29/free-mlb-picks-for-today-10-29-2025/','kickoff_bound':'20251030000000','expected':['Toronto','LA','over 8'],'source_record':'tipster-year overseas prediction 79949176484eff7483fec4f6'},
 {'sport':'bk','kind':'article','source_url':'https://www.betblog.com/picks/773665-golden-state-warriors-la-clippers','kickoff_bound':'20251029030000','expected':['Golden State','Clippers','2.07'],'source_record':'tipster-year basketball betblog-773665'},
 {'sport':'bs','kind':'lineup','source_url':'https://www.koreabaseball.com/ws/Schedule.asmx/GetLineUpAnalysis','kickoff_bound':'20261104093000','expected':['20261104SSKT0'],'source_record':'pregame-baseball lineup-metadata; POST parameters required'},
 {'sport':'sc/bk/bs','kind':'price','source_url':'https://www.betman.co.kr/buyPsblGame/gameInfoInq.do','kickoff_bound':'20250215040000','expected':['250020','63','1.98','3.25'],'source_record':'pregame-soccer betman:250020:63; POST parameters required'}]

def now(): return datetime.now(timezone.utc).isoformat()
def cdx_url(case):
 return 'https://web.archive.org/cdx/search/cdx?' + urllib.parse.urlencode({'url':case['source_url'],'output':'json','filter':'statuscode:200','to':case['kickoff_bound'],'fl':'timestamp,original,statuscode,digest,mimetype','collapse':'digest','limit':'10'})

def fetch(url, index, timeout=12):
 cache=OUT/'requests.json'
 if cache.exists():
  for previous in json.loads(cache.read_text(encoding='utf-8-sig')):
   if previous['url']==url and previous.get('body_file') and (OUT/previous['body_file']).exists(): return previous
 start=now(); status=None; body=b''; error=None
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; research evidence)'})
  with urllib.request.urlopen(req,timeout=timeout) as response:
   status=response.status; body=response.read(4000001)
   if len(body)>4000000: raise ValueError('body exceeds cap')
 except urllib.error.HTTPError as e:
  status=e.code; body=e.read(4000000); error='HTTPError'
 except Exception as e: error=type(e).__name__+': '+str(e)
 finish=now(); sha=hashlib.sha256(body).hexdigest() if body else None
 file=None
 if body:
  file='raw/'+hashlib.sha256(url.encode()).hexdigest()[:20]+'.body.gz'; (OUT/'raw').mkdir(parents=True,exist_ok=True)
  with gzip.open(OUT/file,'wb') as f: f.write(body)
 return {'url':url,'requested_at':start,'body_completed_at':finish if body and error in (None,'HTTPError') else None,'status':status,'body_sha256':sha,'body_file':file,'bytes':len(body),'error':error}

def parse_cdx(body, cutoff):
 try: rows=json.loads(body)
 except (ValueError,TypeError): return []
 if not isinstance(rows,list) or not rows or not isinstance(rows[0],list): return []
 result=[]
 for row in rows[1:]:
  if len(row)>=5 and len(row[0])==14 and row[0].isdigit() and row[0]<cutoff:
   result.append(dict(zip(['capture_timestamp','original','status','digest','mimetype'],row[:5])))
 return result

def classify(capture, cutoff, matched):
 return bool(capture and capture < cutoff and matched)

def main():
 OUT.mkdir(parents=True,exist_ok=True); receipts=[]; results=[]; blocked=False
 for case in CASES:
  result={**case,'archive_capture':None,'archived_content_verified':False,'strict_pregame_proof':False}
  if blocked: result['reason']='Wayback blocked earlier; no retry or bypass'; results.append(result); continue
  rec=fetch(cdx_url(case),len(receipts)); receipts.append(rec)
  if rec['status'] in (403,429):
   blocked=True; result['reason']='Wayback access blocked; stopped'; results.append(result); continue
  if not rec['body_file']:
   result['reason']='CDX unavailable'; results.append(result); continue
  with gzip.open(OUT/rec['body_file'],'rb') as f: captures=parse_cdx(f.read(),case['kickoff_bound'])
  result['captures_before_conservative_cutoff']=captures
  if not captures: result['reason']='no eligible CDX capture'; results.append(result); continue
  capture=captures[-1]; result['archive_capture']=capture
  archive_url='https://web.archive.org/web/'+capture['capture_timestamp']+'id_/'+capture['original']
  body_rec=fetch(archive_url,len(receipts)); receipts.append(body_rec)
  if body_rec['status'] in (403,429): blocked=True
  if body_rec['status']==200 and body_rec['body_file']:
   with gzip.open(OUT/body_rec['body_file'],'rb') as f: content=f.read().decode('utf-8','replace').casefold()
   matches={x:x.casefold() in content for x in case['expected']}; result['expected_field_matches']=matches
   result['archived_content_verified']=all(matches.values()); result['archive_body_sha256']=body_rec['body_sha256']
  result['reason']='capture requires fixture, exact field, and timestamp validation' if not result['archived_content_verified'] else 'field strings found; manual fixture and source semantics review required'
  results.append(result)
 (OUT/'requests.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding='utf-8')
 (OUT/'proof-records.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'requests':len(receipts),'wayback_blocked':blocked,'cases':len(results)},ensure_ascii=False))
if __name__=='__main__': main()
