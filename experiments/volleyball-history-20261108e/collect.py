"""Fetch four documented public CSVs, once each, with resumable receipts."""
import argparse,hashlib,json,time
from datetime import datetime,timezone
from pathlib import Path
import requests
FILES=[f'{y}/VNL_{sex}_Team_{y}.csv' for y in (2025,2026) for sex in ('Men','Women')]
def collect(output,session=requests,delay=2):
 out=Path(output);out.mkdir(parents=True,exist_ok=True);manifest=out/'manifest.json';state=json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else {};base='https://raw.githubusercontent.com/volleyballdataroom/VNL/main/'
 for f in FILES:
  dest=out/Path(f).name;previous=state.get(f,{})
  if dest.exists() and previous.get('status')=='ok' and hashlib.sha256(dest.read_bytes()).hexdigest()==previous.get('sha256'):continue
  for attempt in range(3):
   try:
    r=session.get(base+f,timeout=20,allow_redirects=False);now=datetime.now(timezone.utc).isoformat();status=r.status_code
    if status in (403,404):state[f]={'status':'blocked' if status==403 else 'not_found','http_status':status,'received_at':now};break
    if status==429 or status>=500:raise requests.RequestException(f'http_{status}')
    r.raise_for_status();b=r.content
    if len(b)>500000 or b'RECEPTION_Attempts' not in b[:2000]:raise ValueError('invalid_csv')
    dest.write_bytes(b);state[f]={'status':'ok','url':base+f,'http_status':status,'received_at':now,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)};break
   except (requests.RequestException,ValueError) as e:
    state[f]={'status':'error','reason':type(e).__name__,'attempts':attempt+1};time.sleep(delay*2**attempt)
  manifest.write_text(json.dumps(state,indent=2)+'\n',encoding='utf8');time.sleep(delay)
 return state
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();collect(a.output)
