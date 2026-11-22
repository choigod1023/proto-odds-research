import collections, gzip, hashlib, json
from datetime import datetime
import re
from bs4 import BeautifulSoup
from pathlib import Path
from archived_2023 import ALIASES, parse, daily_pairs
from recover import TEAMS, unique_daily

OUT=Path('experiments/npb-recovery-20261102')
ALIASES.update({'阪神':('한신','阪神タイガース'),'横浜DeNA':('요코하마','横浜DeNAベイスターズ'),'オリックス':('오릭스','オリックス・バファローズ'),'埼玉西武':('세이부','埼玉西武ライオンズ'),'福岡ソフトバンク':('소프트뱅크','福岡ソフトバンクホークス'),'千葉ロッテ':('지바롯데','千葉ロッテマリーンズ')})

def read_documents():
 docs={}; errors=[]
 for folder in (OUT/'raw',OUT/'expanded_raw'):
  for meta_path in sorted(folder.glob('*.json')):
   try:
    meta=json.loads(meta_path.read_text(encoding='utf8')); q=meta_path.with_suffix('.html') if folder.name=='raw' else meta_path.with_suffix('.html.gz'); b=q.read_bytes() if folder.name=='raw' else gzip.decompress(q.read_bytes()); h=hashlib.sha256(b).hexdigest()
    if h!=meta['sha256']: raise ValueError('hash mismatch')
    if meta['url'] in docs and docs[meta['url']][1]['sha256']!=h: raise ValueError('different bytes for same URL')
    docs[meta['url']]=(b,meta,str(q))
   except (OSError,ValueError,KeyError) as e: errors.append({'path':str(meta_path),'error':str(e)})
 if errors: raise ValueError(errors)
 return docs

def index_complete(raw):
 soup=BeautifulSoup(raw,'html.parser')
 links=[a['href'] for a in soup.find_all('a',href=True) if re.fullmatch(r's\d+\.html',a['href'])]
 pairs=daily_pairs(raw)
 return bool(links) and len(links)==len(pairs) and len(set(links))==len(links)

def run():
 docs=read_documents(); raw=Path('data/raw/npb_starters.json'); base=json.loads(raw.read_text(encoding='utf8')); missing=[r for r in base if not r.get('home_starter') or not r.get('away_starter')]
 counts=collections.Counter((r['date'],r['home'],r['away']) for r in base); log=json.loads((OUT/'collection-log.json').read_text(encoding='utf8')); entries={r['game_id_string']:r for r in log['entries'] if 'game_id_string' in r}; failed_days={r['date'] for r in log['entries'] if r.get('status')=='daily_failed'}; attempted_urls={a['url'] for a in log['attempts']}; result=[]
 for r in missing:
  row={'game_id_string':r['gameId'],'date':r['date'],'home':r['home'],'away':r['away'],'original_start':r['datetime'],'original_home_starter':r['home_starter'],'original_away_starter':r['away_starter'],'original_home_score':r['home_score'],'original_away_score':r['away_score'],'actual_pregame_receipt':'not_verified','same_price_product_time':'not_checked','official_player_id_home':None,'official_player_id_away':None}
  e=entries.get(r['gameId'])
  if r['home'] not in TEAMS or r['away'] not in TEAMS:row['status']='excluded_all_star_or_unknown_team'
  elif not r['date'].startswith('2023'):row['status']='not_attempted_2026'
  elif e is None and r['date'] in failed_days:
   url="https://npb.jp/bis/2023/games/gm"+r['date'].replace('-','')+".html"
   row['status']='daily_failed' if url in attempted_urls else 'not_attempted_budget_or_unlogged_failure'
  elif e is None:row['status']='not_attempted_budget_or_unlogged_failure'
  elif e['daily_url'] not in docs or not index_complete(docs[e['daily_url']][0]):row['status']='daily_index_incomplete'
  elif e['status']=='game_page_failed' and e.get('game_url') not in attempted_urls:row['status']='not_attempted_budget_or_unlogged_failure'
  elif e['status']!='game_page_available':row['status']=e['status']
  elif counts[(r['date'],r['home'],r['away'])]!=1:row['status']='ambiguous_original_pair'
  else:
   try:
    daily=docs[e['daily_url']]; b,meta,path=docs[e['game_url']]; g=parse(b,meta); pp=daily_pairs(daily[0]); key=(TEAMS[r['home']],TEAMS[r['away']]); basename=e['game_url'].rsplit('/',1)[-1]
    if not unique_daily(pp,key,basename):row['status']='ambiguous_or_missing_daily_pair'
    elif g is None:row['status']='game_page_parse_failed'
    elif (g['date'],g['home'],g['away'])!=(r['date'],r['home'],r['away']):row['status']='official_identity_conflict'
    else:
     t=datetime.fromisoformat(r['datetime']); h,m=map(int,g['official_start_hhmm'].split(':')); delta=abs((t-t.replace(hour=h,minute=m,second=0,microsecond=0)).total_seconds())
     if delta>1800:row['status']='start_time_diff_gt_30m'
     else:
      row.update(g); row.update({'status':'retrospective_names_recovered','source_raw_path':path,'daily_sha256':daily[1]['sha256'],'daily_raw_path':daily[2],'start_delta_seconds':delta,'score_check':'matches' if (g['home_score'],g['away_score'])==(r['home_score'],r['away_score']) else 'differs'})
   except KeyError:row['status']='archived_document_missing'
  result.append(row)
 (OUT/'cohort-expanded.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in result),encoding='utf8')
 recovered=[x for x in result if x['status']=='retrospective_names_recovered']
 doclist=[{'url':u,'sha256':m['sha256'],'observed_at':m['observed_at'],'path':p} for u,(_,m,p) in sorted(docs.items())]
 summary={'input_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'base_rows':len(base),'missing_rows':len(missing),'missing_by_year':dict(collections.Counter(x['date'][:4] for x in missing)),'recovered_dates':len({x['date'] for x in recovered}),'recovered_date_min':min((x['date'] for x in recovered),default=None),'recovered_date_max':max((x['date'] for x in recovered),default=None),'statuses':dict(collections.Counter(x['status'] for x in result)),'recovered':len(recovered),'score_checks':dict(collections.Counter(x['score_check'] for x in recovered)),'both_names_before':sum(bool(x.get('home_starter') and x.get('away_starter')) for x in base),'official_player_id_pairs_recovered':0,'actual_pregame_receipt_recovered':0,'same_price_product_time':None,'network_http_statuses':dict(collections.Counter(str(x['status']) for x in log['attempts'])),'network_attempts':log['network_attempts'],'network_budget':log['budget'],'elapsed_seconds':log['elapsed_seconds'],'stop_reason':log['halted'],'source_documents':doclist,'scope':'2023 archive, postgame retrospective; 2026 not requested; no odds/operating picks joined'}
 (OUT/'manifest-expanded.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 print(json.dumps({k:v for k,v in summary.items() if k!='source_documents'},ensure_ascii=False,indent=2))
if __name__=='__main__':run()
