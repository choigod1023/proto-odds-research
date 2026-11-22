"""Resume only discovered public schedule IDs; stop on any error or rate limit."""
import base64,hashlib,json,time,datetime,sys
from pathlib import Path
import requests
from kovo_parse import parse

def collect(schedule_file,output):
 data=json.loads(Path(schedule_file).read_text(encoding='utf8'))['payload'];out=Path(output);out.mkdir(parents=True,exist_ok=True);manifest=out/'manifest.json';state=json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else {};s=requests.Session()
 for row in data:
  if row.get('endTime') is None:continue
  ident=f"{row['season']['value']}:{row['league']['value']}:{row['number']}";pid=base64.b64encode(f'{ident:<12}'.encode()).decode()[::-1];f=out/(pid+'.html');prior=state.get(pid,{})
  if prior.get('status')=='ok' and f.exists() and hashlib.sha256(f.read_bytes()).hexdigest()==prior.get('sha256'):continue
  url='https://dbbank.kovo.co.kr/game/11110_pop_result.asp?PID='+pid
  try:
   r=s.get(url,timeout=20,allow_redirects=False);at=datetime.datetime.now(datetime.timezone.utc).isoformat()
   if r.status_code!=200:state[pid]={'status':'stopped_http','http_status':r.status_code,'url':url,'received_at':at};manifest.write_text(json.dumps(state,indent=2),encoding='utf8');print('STOP',r.status_code,flush=True);break
   body=r.content
   if len(body)>1000000:raise ValueError('oversize')
   parsed=parse(body,url)
   if parsed['date']!=row['gameDate'] or parsed['official_game_number']!=row['number'] or parsed['sets_won']!=[row['homeTeam']['score'],row['awayTeam']['score']]:raise ValueError('schedule_score_mismatch')
   f.write_bytes(body);state[pid]={'status':'ok','url':url,'received_at':at,'sha256':hashlib.sha256(body).hexdigest(),'number':row['number'],'date':row['gameDate'],'gender':row['gender']};manifest.write_text(json.dumps(state,indent=2),encoding='utf8');
   if len(state)%20==0:print('received',len(state),'of',len(data),flush=True)
   time.sleep(2)
  except (requests.RequestException,ValueError,KeyError,TypeError,IndexError) as e:
   state[pid]={'status':'stopped','reason':type(e).__name__+':'+str(e),'url':url};manifest.write_text(json.dumps(state,indent=2),encoding='utf8');print('STOP',type(e).__name__,str(e),flush=True);break
if __name__=='__main__':collect(sys.argv[1],sys.argv[2])
