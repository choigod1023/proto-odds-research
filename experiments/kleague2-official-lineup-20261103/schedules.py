"""Capture two monthly official schedules for cross-checking historical IDs."""
import hashlib,json,time
from datetime import datetime,timezone
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parent/'raw'
URL='https://www.kleague.com/getScheduleList.do'
for y,m in [(2024,'03'),(2026,'02')]:
 stem=f'{y}-2-schedule-{m}'; raw=ROOT/(stem+'.bin'); meta=ROOT/(stem+'.json')
 if raw.exists() or meta.exists():
  if not raw.exists() or not meta.exists() or hashlib.sha256(raw.read_bytes()).hexdigest()!=json.loads(meta.read_text(encoding='utf8'))['sha256']: raise RuntimeError('invalid cache')
  continue
 params={'leagueId':'2','teamId':'','ticketStatus':'','year':str(y),'month':m,'ticketYn':'N'}
 r=requests.post(URL,json=params,timeout=15,allow_redirects=False,stream=True)
 if r.status_code!=200: r.close(); raise RuntimeError(f'stopped HTTP {r.status_code}')
 chunks=[]; size=0
 for c in r.iter_content(65536):
  size+=len(c)
  if size>3_000_000: r.close(); raise RuntimeError('response too large')
  chunks.append(c)
 received=datetime.now(timezone.utc).isoformat(); body=b''.join(chunks);r.close()
 raw.write_bytes(body);meta.write_text(json.dumps({'method':'POST','url':URL,'json':params,'status':200,'received_at':received,'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'receipt_independently_verified':False},indent=2)+'\n',encoding='utf8')
 print(stem,len(body),flush=True);time.sleep(1)
