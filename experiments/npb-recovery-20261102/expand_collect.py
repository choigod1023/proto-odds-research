import collections, datetime, gzip, hashlib, json, pathlib, re, time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

OUT=pathlib.Path('experiments/npb-recovery-20261102'); RAW=OUT/'expanded_raw'; RAW.mkdir(exist_ok=True)
TEAM={'한신':'t','요코하마':'db','요미우리':'g','주니치':'d','히로시마':'c','야쿠르트':'s','소프트뱅크':'h','닛폰햄':'f','오릭스':'b','라쿠텐':'e','세이부':'l','지바롯데':'m'}
BUDGET=300; DEADLINE=900; SLEEP=.45
rows=json.loads(pathlib.Path('data/raw/npb_starters.json').read_text(encoding='utf8')); rows=[r for r in rows if r['date'].startswith('2023') and (not r.get('home_starter') or not r.get('away_starter')) and r['home'] in TEAM and r['away'] in TEAM]
byday=collections.defaultdict(list)
for r in rows: byday[r['date']].append(r)
cache={}
for p in list((OUT/'raw').glob('*.json'))+list(RAW.glob('*.json')):
 try:
  m=json.loads(p.read_text(encoding='utf8')); raw=p.with_suffix('.html') if p.parent.name=='raw' else p.with_suffix('.html.gz')
  b=raw.read_bytes() if raw.suffix=='.html' else gzip.decompress(raw.read_bytes())
  if hashlib.sha256(b).hexdigest()==m['sha256']: cache[m['url']]=(b,m)
 except (OSError,KeyError,ValueError): pass
attempts=[]; first=time.monotonic(); last=0; consecutive=0; halted=None; session=requests.Session(); session.headers.update({'User-Agent':'NPB research archival validation (single client)'})
def get(url):
 global last,consecutive,halted
 if url in cache:return cache[url][0]
 if len(attempts)>=BUDGET: halted='request_budget'; return None
 if time.monotonic()-first>=DEADLINE: halted='time_budget'; return None
 wait=SLEEP-(time.monotonic()-last)
 if wait>0:time.sleep(wait)
 started=datetime.datetime.now(datetime.timezone.utc).isoformat(); status=None; error=None; content=None
 try:
  r=session.get(url,timeout=15); status=r.status_code
  if status==200:content=r.content
 except requests.RequestException as e: error=type(e).__name__
 last=time.monotonic(); now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 a={'url':url,'started_at':started,'observed_at':now,'status':status,'error':error}; attempts.append(a)
 if content is None:
  consecutive+=1
  if status in (403,429): halted=f'http_{status}'
  elif consecutive>=3:halted='three_consecutive_failures'
  return None
 consecutive=0; h=hashlib.sha256(content).hexdigest(); a.update({'sha256':h,'bytes':len(content)})
 name=hashlib.sha256(url.encode()).hexdigest(); dest=RAW/(name+'.html.gz'); meta=RAW/(name+'.json')
 if not dest.exists() and not meta.exists(): dest.write_bytes(gzip.compress(content,mtime=0)); meta.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 cache[url]=(content,a); return content

def pairs(b):
 s=BeautifulSoup(b,'html.parser'); out=[]
 for a in s.find_all('a',href=True):
  if not re.fullmatch(r's\d+\.html',a['href']):continue
  tr=a.find_parent('tr'); prev=tr.find_previous_sibling('tr') if tr else None
  imgs=prev.find_all('img',src=True) if prev else []
  codes=[m.group(1) for im in imgs if (m:=re.search(r'pet2023_([a-z]+)_',im['src']))]
  if len(codes)==2:out.append((a['href'],tuple(codes)))
 return out

log=[]
for day,games in sorted(byday.items()):
 if halted:break
 u=f"https://npb.jp/bis/2023/games/gm{day.replace('-','')}.html"; b=get(u)
 if b is None:
  log.append({'date':day,'status':'not_attempted_budget' if halted=='request_budget' else 'daily_failed','candidates':len(games)});continue
 pp=pairs(b); paircounts=collections.Counter((r['home'],r['away']) for r in games)
 for r in games:
  if halted:break
  key=(TEAM[r['home']],TEAM[r['away']]); links=[link for link,team in pp if team==key]
  entry={'date':day,'game_id_string':r['gameId'],'daily_url':u,'daily_pair_count':len(links)}
  if paircounts[(r['home'],r['away'])]!=1 or len(links)>1:entry['status']='ambiguous_pair'
  elif not links:entry['status']='not_found_daily_index'
  else:
   target=urljoin(u,links[0]); entry['game_url']=target
   raw=get(target)
   entry['status']='game_page_available' if raw is not None else ('not_attempted_budget' if halted=='request_budget' else 'game_page_failed')
  log.append(entry)
 if len(attempts)%20<7:
  print('requests',len(attempts),'days_complete_through',day,'logged',len(log),flush=True)
(OUT/'collection-log.json').write_text(json.dumps({'budget':BUDGET,'time_limit_seconds':DEADLINE,'min_spacing_seconds':SLEEP,'network_attempts':len(attempts),'elapsed_seconds':round(time.monotonic()-first,2),'halted':halted,'attempts':attempts,'entries':log},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print('FINISHED',json.dumps({'network_attempts':len(attempts),'elapsed_seconds':round(time.monotonic()-first,2),'halted':halted,'entries':len(log),'statuses':dict(collections.Counter(x['status'] for x in log))},ensure_ascii=False),flush=True)
