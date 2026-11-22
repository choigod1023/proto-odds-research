import collections,datetime,gzip,hashlib,json,pathlib,time
import requests
from official_2026 import daily_pairs_2026
from recover import TEAMS
OUT=pathlib.Path('experiments/npb-recovery-20261102'); RAW=OUT/'continuation_raw2'; RAW.mkdir(exist_ok=True)
prior=json.loads((OUT/'collection-log-continuation.json').read_text(encoding='utf8'))
first=datetime.datetime.fromisoformat(prior['attempts'][0]['started_at']) if prior['attempts'] else datetime.datetime.now(datetime.timezone.utc)
budget=max(0,1000-prior['network_attempts']); cache={}
for folder in ('raw','expanded_raw','continuation_raw','continuation_raw2'):
 for p in (OUT/folder).glob('*.json'):
  try:
   m=json.loads(p.read_text(encoding='utf8')); q=p.with_suffix('.html') if folder=='raw' else p.with_suffix('.html.gz'); b=q.read_bytes() if folder=='raw' else gzip.decompress(q.read_bytes())
   if hashlib.sha256(b).hexdigest()==m['sha256']:cache[m['url']]=b
  except (KeyError,ValueError,OSError):pass
base=json.loads(pathlib.Path('data/raw/npb_starters.json').read_text(encoding='utf8')); rows=[r for r in base if r['date'].startswith('2026') and (not r.get('home_starter') or not r.get('away_starter')) and r['home'] in TEAMS and r['away'] in TEAMS]
byday=collections.defaultdict(list)
for r in rows:byday[r['date']].append(r)
counts=collections.Counter((r['date'],r['home'],r['away']) for r in base); attempts=[]; entries=[]; halted=prior['halted'] if prior['halted'] in ('http_403','http_429','three_consecutive_failures') else None; consecutive=0; last=0; sess=requests.Session();sess.headers.update({'User-Agent':'NPB research archival validation (single client)'})
def get(url):
 global halted,consecutive,last
 if url in cache:return cache[url]
 if len(attempts)>=budget:halted='request_budget';return None
 if (datetime.datetime.now(datetime.timezone.utc)-first).total_seconds()>=900:halted='time_budget';return None
 delay=.45-(time.monotonic()-last)
 if delay>0:time.sleep(delay)
 started=datetime.datetime.now(datetime.timezone.utc).isoformat(); status=None; error=None; content=None
 try:
  r=sess.get(url,timeout=15); status=r.status_code
  if status==200:content=r.content
 except requests.RequestException as e:error=type(e).__name__
 last=time.monotonic(); m={'url':url,'started_at':started,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':status,'error':error}; attempts.append(m)
 if content is None:
  consecutive+=1
  if status in (403,429):halted=f'http_{status}'
  elif consecutive>=3:halted='three_consecutive_failures'
  return None
 consecutive=0; m.update({'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}); name=hashlib.sha256(url.encode()).hexdigest(); f=RAW/(name+'.html.gz'); j=RAW/(name+'.json')
 if not f.exists() and not j.exists():f.write_bytes(gzip.compress(content,mtime=0));j.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 cache[url]=content;return content
for day,games in sorted(byday.items()):
 if halted:break
 u=f"https://npb.jp/bis/2026/games/gm{day.replace('-','')}.html"; b=get(u)
 if b is None:
  for r in games:entries.append({'game_id_string':r['gameId'],'date':day,'daily_url':u,'status':'not_attempted' if halted in ('request_budget','time_budget') else 'daily_failed'})
  continue
 pp=daily_pairs_2026(b,day)
 for r in games:
  if halted:break
  key=(TEAMS[r['home']],TEAMS[r['away']]); urls=[url for url,k in pp if k==key]; e={'game_id_string':r['gameId'],'date':day,'daily_url':u,'daily_pair_count':len(urls)}
  if counts[(day,r['home'],r['away'])]!=1 or len(urls)>1:e['status']='ambiguous_pair'
  elif not urls:e['status']='not_found_daily_index'
  else:
   e['game_url']=urls[0]; page=get(urls[0]);e['status']='game_page_available' if page is not None else ('not_attempted' if halted in ('request_budget','time_budget') else 'game_page_failed')
  entries.append(e)
 print('2026',day,'new requests',len(attempts),'entries',len(entries),flush=True)
report={'prior_network_attempts':prior['network_attempts'],'network_attempts':len(attempts),'combined_network_attempts':prior['network_attempts']+len(attempts),'max_combined_attempts':1000,'elapsed_since_first_attempt_seconds':round((datetime.datetime.now(datetime.timezone.utc)-first).total_seconds(),2),'halted':halted,'attempts':attempts,'entries':entries}
(OUT/'collection-log-2026.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print('FINISHED',json.dumps({k:v for k,v in report.items() if k not in ('entries','attempts')}),flush=True)
