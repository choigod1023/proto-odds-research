import collections,gzip,hashlib,json,re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from archived_2023 import parse, daily_pairs
from expanded_recover import index_complete
from official_2026 import daily_pairs_2026,parse_2026,TEAMS as OFFICIAL_TEAMS
from recover import TEAMS,unique_daily
OUT=Path('experiments/npb-recovery-20261102')

def read_docs():
 docs={}
 for folder in ('raw','expanded_raw','continuation_raw','continuation_raw2'):
  for p in sorted((OUT/folder).glob('*.json')):
   m=json.loads(p.read_text(encoding='utf8')); q=p.with_suffix('.html') if folder=='raw' else p.with_suffix('.html.gz'); b=q.read_bytes() if folder=='raw' else gzip.decompress(q.read_bytes())
   if hashlib.sha256(b).hexdigest()!=m['sha256']:raise ValueError('source hash mismatch: '+str(q))
   if m['url'] in docs and docs[m['url']][1]['sha256']!=m['sha256']:raise ValueError('conflicting bytes for '+m['url'])
   docs[m['url']]=(b,m,str(q))
 return docs

def index_complete_2026(raw,day):
 s=BeautifulSoup(raw,'html.parser'); paths=[a['href'] for a in s.find_all('a',href=True) if re.fullmatch(r'/scores/2026/\d{4}/[a-z]+-[a-z]+-\d+/',urlparse(a['href']).path)]
 pairs=daily_pairs_2026(raw,day)
 return bool(paths) and len(paths)==len(pairs) and len(paths)==len(set(paths))

def pitcher_links_2023(raw):
 s=BeautifulSoup(raw,'html.parser'); tables=[t for t in s.select('table.gmtbltop') if t.get_text(' ',strip=True).startswith('投 回')]; links=[]
 if len(tables)!=2:return [None,None]
 for t in tables:
  rows=t.find_all('tr'); cells=rows[1].find_all(['td','th'],recursive=False) if len(rows)>1 else []
  a=cells[1].find('a',href=True) if len(cells)>1 else None
  m=re.fullmatch(r'/bis/players/(\d+)\.html',urlparse(a['href']).path) if a else None
  links.append({'name':a.get_text(' ',strip=True),'player_id':m[1],'player_url':'https://npb.jp'+urlparse(a['href']).path,'role':'starter'} if m else None)
 return links

def run():
 docs=read_docs(); raw=Path('data/raw/npb_starters.json'); base=json.loads(raw.read_text(encoding='utf8')); missing=[r for r in base if not r.get('home_starter') or not r.get('away_starter')]; counts=collections.Counter((r['date'],r['home'],r['away']) for r in base)
 log1=json.loads((OUT/'collection-log-continuation.json').read_text(encoding='utf8')); log2=json.loads((OUT/'collection-log-2026.json').read_text(encoding='utf8')); entries={e['game_id_string']:e for log in (log1,log2) for e in log['entries'] if 'game_id_string' in e}; failed={e['date']:e['status'] for log in (log1,log2) for e in log['entries'] if 'game_id_string' not in e}; rows=[]
 for r in missing:
  row={'game_id_string':r['gameId'],'date':r['date'],'home':r['home'],'away':r['away'],'original_start':r['datetime'],'original_home_score':r['home_score'],'original_away_score':r['away_score'],'original_home_starter':r['home_starter'],'original_away_starter':r['away_starter'],'actual_pregame_receipt':'not_verified','same_price_product_time':'not_checked','official_player_id_home':None,'official_player_id_away':None}; e=entries.get(r['gameId']); day=r['date']; year=day[:4]
  if r['home'] not in TEAMS or r['away'] not in TEAMS:row['status']='excluded_all_star_or_unknown_team'
  elif e is None:row['status']=failed.get(day,'not_attempted')
  elif e['daily_url'] not in docs:row['status']=e['status'] if e['status'] in ('daily_failed','not_attempted') else 'daily_document_missing'
  else:
   daily=docs[e['daily_url']]; good_index=index_complete(daily[0]) if year=='2023' else index_complete_2026(daily[0],day)
   if not good_index:row['status']='daily_index_incomplete'
   elif e['status']!='game_page_available':row['status']=e['status']
   elif counts[(day,r['home'],r['away'])]!=1:row['status']='ambiguous_original_pair'
   elif e.get('game_url') not in docs:row['status']='game_document_missing'
   else:
    b,meta,path=docs[e['game_url']]; pair=(TEAMS[r['home']],TEAMS[r['away']])
    if year=='2023':
     g=parse(b,meta); pp=daily_pairs(daily[0]); unique=unique_daily(pp,pair,e['game_url'].rsplit('/',1)[-1]); identity=bool(g and (g['date'],g['home'],g['away'])==(day,r['home'],r['away']))
     if g:
      away,home=pitcher_links_2023(b)
      if home and home['name']==g['home_starter_name']:home['team_id']=pair[0]
      else:home=None
      if away and away['name']==g['away_starter_name']:away['team_id']=pair[1]
      else:away=None
      g['home_starter_player']=home;g['away_starter_player']=away
    else:
     g=parse_2026(b,e['game_url'],day); pp=daily_pairs_2026(daily[0],day); unique=unique_daily(pp,pair,e['game_url']); identity=bool(g and (g['official_home_team_id'],g['official_away_team_id'])==pair)
    if not unique:row['status']='ambiguous_or_missing_daily_pair'
    elif not g and year=='2026' and '中止' in BeautifulSoup(b,'html.parser').get_text(' ',strip=True):
     text=BeautifulSoup(b,'html.parser').get_text(' ',strip=True); names={v:k for k,v in OFFICIAL_TEAMS.items()}; dm=re.search(r'2026年\s*(\d+)月\s*(\d+)日',text)
     if dm and (int(dm[1]),int(dm[2]))==(int(day[5:7]),int(day[8:10])) and names[pair[0]] in text and names[pair[1]] in text:
      row.update({'status':'official_cancelled','source_url':meta['url'],'source_sha256':meta['sha256'],'source_observed_at':meta['observed_at'],'source_raw_path':path,'daily_sha256':daily[1]['sha256']})
     else:row['status']='game_page_parse_failed'
    elif not g:row['status']='game_page_parse_failed'
    elif not identity:row['status']='official_identity_conflict'
    else:
     t=datetime.fromisoformat(r['datetime']); h,m=map(int,g['official_start_hhmm'].split(':')); delta=abs((t-t.replace(hour=h,minute=m,second=0,microsecond=0)).total_seconds())
     if delta>1800:row.update({'status':'start_time_diff_gt_30m','start_delta_seconds':delta,'official_start_hhmm':g['official_start_hhmm'],'source_url':meta['url'],'source_sha256':meta['sha256'],'source_observed_at':meta['observed_at'],'source_raw_path':path,'daily_sha256':daily[1]['sha256'],'candidate_home_starter_name':g['home_starter_name'],'candidate_away_starter_name':g['away_starter_name'],'candidate_official_home_score':g['home_score'],'candidate_official_away_score':g['away_score'],'candidate_score_matches':(g['home_score'],g['away_score'])==(r['home_score'],r['away_score'])})
     else:
      row.update(g); hp=g.get('home_starter_player');ap=g.get('away_starter_player'); row.update({'status':'retrospective_names_recovered','source_url':meta['url'],'source_sha256':meta['sha256'],'source_observed_at':meta['observed_at'],'source_raw_path':path,'daily_sha256':daily[1]['sha256'],'daily_raw_path':daily[2],'start_delta_seconds':delta,'score_check':'matches' if (g['home_score'],g['away_score'])==(r['home_score'],r['away_score']) else 'differs','official_player_id_home':hp['player_id'] if hp else None,'official_player_id_away':ap['player_id'] if ap else None})
  rows.append(row)
 (OUT/'cohort-continuation.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in rows),encoding='utf8')
 good=[x for x in rows if x['status']=='retrospective_names_recovered']; source=[{'url':u,'sha256':m['sha256'],'observed_at':m['observed_at'],'path':p} for u,(_,m,p) in sorted(docs.items())]; old=json.loads((OUT/'collection-log.json').read_text(encoding='utf8'))
 manifest={'input_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'base_rows':len(base),'missing_rows':len(missing),'statuses':dict(collections.Counter(x['status'] for x in rows)),'statuses_by_year':{year:dict(collections.Counter(x['status'] for x in rows if x['date'].startswith(year))) for year in ('2023','2026')},'recovered':len(good),'recovered_by_year':dict(collections.Counter(x['date'][:4] for x in good)),'score_checks':dict(collections.Counter(x['score_check'] for x in good)),'both_player_ids':sum(bool(x['official_player_id_home'] and x['official_player_id_away']) for x in good),'one_or_more_player_ids':sum(bool(x['official_player_id_home'] or x['official_player_id_away']) for x in good),'actual_pregame_receipt_recovered':0,'same_price_product_time':None,'prior_network_attempts':old['network_attempts'],'new_network_attempts':log1['network_attempts']+log2['network_attempts'],'all_network_attempts':old['network_attempts']+log1['network_attempts']+log2['network_attempts'],'new_http_statuses':dict(collections.Counter(str(x['status']) for x in log1['attempts']+log2['attempts'])),'stop_reason_first':log1['halted'],'stop_reason_second':log2['halted'],'new_elapsed_seconds':log2['elapsed_since_first_attempt_seconds'],'source_documents':source,'scope':'official results collected postgame; no pregame receipt or price join'}
 (OUT/'manifest-continuation.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({k:v for k,v in manifest.items() if k!='source_documents'},ensure_ascii=False,indent=2))
if __name__=='__main__':run()
