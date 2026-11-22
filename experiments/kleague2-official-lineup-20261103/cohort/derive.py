"""Offline reconciliation; score and substitutions are diagnostics, never prematch features."""
import collections,datetime,gzip,hashlib,json,re
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2];RAW=ROOT/'raw'
def sha(b):return hashlib.sha256(b).hexdigest()
def write(p,obj):p.write_bytes((json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8'))
def raw(stem):
 data=(RAW/(stem+'.gz')).read_bytes();m=json.loads((RAW/(stem+'.json')).read_bytes());body=gzip.decompress(data)
 if sha(data)!=m['stored_sha256'] or sha(body)!=m['source_sha256']:raise ValueError('hash mismatch')
 return body,m

def parse_match(fixture):
 year=fixture['year'];game=fixture['gameId'];body,meta=raw(f'match-{year}-2-{game}')
 soup=BeautifulSoup(body.decode('utf8'),'html.parser')
 for sel,val in [('#year',str(year)),('#meetSeq','2'),('#gameId',str(game))]:
  x=soup.select(sel+' option[selected]')
  if len(x)!=1 or x[0].get('value')!=val:raise ValueError('selected identity '+sel)
 ts=soup.select('.game-info > .team')
 if len(ts)!=2:raise ValueError('team count')
 team_ids=[];names=[]
 for t in ts:
  ids=[c for c in t.get('class',[]) if re.fullmatch(r'K\d+',c)]
  if len(ids)!=1:raise ValueError('team ID')
  team_ids+=ids;names.append(t.select_one('p').get_text(strip=True))
 if team_ids!=[fixture['homeTeam'],fixture['awayTeam']]:raise ValueError('team mismatch')
 dt=soup.select_one('.team-versus .versus p')
 if not dt or fixture['gameDate'].replace('.','/') not in dt.get_text(' ',strip=True):raise ValueError('date mismatch')
 block=soup.select_one('.match-lineup-player .player'); sides=block.find_all('div',recursive=False) if block else []
 if len(sides)!=2:raise ValueError('lineup sides')
 teams=[]
 for i,side in enumerate(sides):
  boxes=side.find_all('div',class_='box',recursive=False)
  if len(boxes)!=2:raise ValueError('lineup groups')
  groups=[]
  for box in boxes:
   group=[]
   for tr in box.select('tr[onclick]'):
    m=re.fullmatch(r"playerDetailPop\('([^']+)',\s*'([^']+)'\)",tr.get('onclick','').strip())
    if not m:continue
    team,pid=m.groups(); cells=tr.find_all('td',recursive=False)
    if team!=team_ids[i] or not re.fullmatch(r'\d{8}',pid) or len(cells)<3:raise ValueError('player identity')
    minute=tr.select_one('.changeM_'+pid);other=tr.select_one('.changeP_'+pid)
    group.append({'player_id':pid,'name':cells[2].get_text(strip=True),'number':cells[0].get_text(strip=True),'position':cells[1].get_text(strip=True),'postmatch_substitution_minute':minute.get_text(strip=True) if minute else '', 'postmatch_substitution_partner_name':other.get_text(strip=True) if other else ''})
   groups.append(group)
  ids=[p['player_id'] for g in groups for p in g]
  if len(groups[0])!=11 or len(ids)!=len(set(ids)):raise ValueError('missing eleven or duplicate player')
  allplayers=groups[0]+groups[1]
  for p in allplayers:
   partner=p['postmatch_substitution_partner_name']; candidates=[q['player_id'] for q in allplayers if q['name'].replace('(c)','').strip()==partner] if partner else []
   p['postmatch_substitution_partner_id']=candidates[0] if len(candidates)==1 else None
  teams.append({'team_id':team_ids[i],'schedule_name':fixture['homeTeamName' if i==0 else 'awayTeamName'],'page_name':names[i],'starting_eleven':groups[0],'bench':groups[1]})
 return {'fixture_id':f'kleague:{year}:2:{game}','kickoff_kst':fixture['gameDate'].replace('.','-')+'T'+fixture['gameTime']+':00+09:00','official_result':[fixture['homeGoal'],fixture['awayGoal']],'round_id':fixture['roundId'],'received_at':meta['received_at'],'lineup_status':'retrospective_official_actual_starting_eleven','pregame_announcement_confirmed':False,'publication_at':None,'source_sha256':meta['source_sha256'],'teams':teams}

def main():
 allgames=[]; errors=[]; summaries={};fixtures=[]
 for year in (2026,2025):
  cp=ROOT/f'census-{year}.json'
  if not cp.exists():continue
  census=json.loads(cp.read_bytes())['fixtures'];fixtures+=census
  ended=[f for f in census if f.get('endYn')=='Y' and f.get('gameStatus')=='FE'];captured=[];missing=[];failed=[];games=[]
  for f in ended:
   stem=f'match-{year}-2-{f["gameId"]}'
   if not (RAW/(stem+'.gz')).exists():missing.append(f['gameId']);continue
   captured.append(f['gameId'])
   try:games.append(parse_match(f))
   except (ValueError,KeyError,IndexError,TypeError) as ex:failed.append({'game_id':f['gameId'],'reason':str(ex)})
  allgames+=games;errors+=failed
  subs=[p for g in games for t in g['teams'] for p in t['starting_eleven']+t['bench'] if p['postmatch_substitution_minute'] and p['postmatch_substitution_partner_name']]
  summaries[str(year)]={'census':len(census),'finished':len(ended),'not_finished':len(census)-len(ended),'captured':len(captured),'complete_lineups':len(games),'parse_failed':len(failed),'not_captured':len(missing),'missing_game_ids':missing,'parse_errors':failed,'starting_player_rows':sum(len(t['starting_eleven']) for g in games for t in g['teams']),'bench_player_rows':sum(len(t['bench']) for g in games for t in g['teams']),'substitution_player_rows':len(subs),'unresolved_partner_ids':sum(p['postmatch_substitution_partner_id'] is None for p in subs),'certified_historical_pregame':0}
 targetpath=REPO/'experiments/accuracy-review-20261030/inputs/targets.jsonl.gz'
 with gzip.open(targetpath,'rt',encoding='utf8') as f:targets=[dict(json.loads(line),_input_line=i) for i,line in enumerate(f,1)]
 targets=[t for t in targets if t.get('league')=='K\ub9ac\uadf82' and t.get('kickoff','')[:4] in ('2025','2026')]
 predpath=REPO/'experiments/accuracy-review-20261030/inputs/predictions.jsonl.gz'
 with gzip.open(predpath,'rt',encoding='utf8') as f:predictions=[dict(json.loads(line),_input_line=i) for i,line in enumerate(f,1)]
 index={f"kleague:{f['year']}:2:{f['gameId']}":f for f in fixtures}; parsed={g['fixture_id']:g for g in allgames};bridge=[]
 for t in targets:
  eid=t.get('event_id'); official=t.get('identity_bridge',{}).get('official_fixture_id'); f=index.get(official); direct=index.get(eid)
  date_equal=bool(f and f['gameDate'].replace('.','-')==t.get('kickoff','')[:10])
  name_equal=bool(f and [f['homeTeamName'],f['awayTeamName']]==[t.get('home'),t.get('away')])
  odds=t.get('odds');price_ok=t.get('sides')==['home','draw','away'] and isinstance(odds,list) and len(odds)==3 and all(isinstance(n,(int,float)) and not isinstance(n,bool) and n>1 for n in odds)
  score_equal=bool(f and [0 if f['homeGoal']>f['awayGoal'] else 1 if f['homeGoal']==f['awayGoal'] else 2][0]==t.get('outcome')) if f and f.get('homeGoal') is not None and f.get('awayGoal') is not None else False
  kickoff_equal=bool(f and f['gameDate'].replace('.','-')+'T'+f['gameTime']==t.get('kickoff','')[:16])
  associated=[p for p in predictions if p.get('event_id')==eid and p.get('league')==t.get('league') and p.get('sides')==t.get('sides') and p.get('kickoff')==t.get('kickoff') and p.get('home')==t.get('home') and p.get('away')==t.get('away') and p.get('odds')==odds and p.get('outcome')==t.get('outcome')]
  bridge.append({'target_event_id':eid,'target_input_line':t['_input_line'],'bridge_fixture_id':official,'direct_event_id_agrees':bool(direct and eid==official),'official_fixture_found':bool(f),'date_equal':date_equal,'kickoff_equal':kickoff_equal,'team_names_exact':name_equal,'score_outcome_equal':score_equal,'complete_3way_odds':price_ok,'odds':odds,'price_id':t.get('price_id'),'product_id':t.get('product_id'),'period':t.get('period'),'lineup_captured':official in parsed,'source_receipt_certified':t.get('source_receipt_certified',False),'price_evidence':t.get('price_evidence'),'cohort':t.get('cohort'),'prediction_rows':[{'line':p['_input_line'],'mode':p.get('mode'),'model':p.get('model')} for p in associated],'prediction_ids':None,'prediction_id_reason':'source records have no distinct prediction ID; input lines identify retrospective recalculations, not frozen operating predictions'})
 lines=b''.join((json.dumps(g,ensure_ascii=False,sort_keys=True)+'\n').encode('utf8') for g in allgames)
 (ROOT/'lineups.jsonl').write_bytes(lines);write(ROOT/'price-bridge.json',bridge)
 summary={'seasons':summaries,'review_input_sha256':sha(targetpath.read_bytes()),'review_prediction_input_sha256':sha(predpath.read_bytes()),'review_k2_targets':len(targets),'review_direct_id_matches':sum(x['direct_event_id_agrees'] for x in bridge),'review_prediction_rows':sum(len(x['prediction_rows']) for x in bridge),'review_identity_matched_lineup_3way_and_result':sum(all(x[k] for k in ('direct_event_id_agrees','date_equal','kickoff_equal','team_names_exact','score_outcome_equal','complete_3way_odds','lineup_captured')) for x in bridge),'review_bridge':bridge,'warning':'postmatch source, pregame reception not certified; target fixture postmatch features forbidden'}
 write(ROOT/'summary.json',summary)
 print(json.dumps({'seasons':summaries,'review_targets':len(targets),'direct_id':summary['review_direct_id_matches'],'matched':summary['review_identity_matched_lineup_3way_and_result']},ensure_ascii=True))
if __name__=='__main__':main()
