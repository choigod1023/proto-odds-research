"""Audit pinned 2025 official lineup, starter and identity evidence offline."""
import base64,collections,gzip,hashlib,json,subprocess
from functools import lru_cache
from pathlib import Path
from bs4 import BeautifulSoup
from official_parse import confirmed_lineups
from audit_2023_source import profile,textrow
from audit_2024_source import identity_only
DATA=Path(r'C:\Users\user\Documents\Codex\kbt25');BASE=Path(r'C:\Users\user\Documents\Codex\kbodata');C='11aa00812d45fdcf35e462413acfc48935dd530d';B='13291d23e855e0d500327cb39ac64a480e395837';P='experiments/kbo-actual-lineups-2025-20261103/'
def git(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def readgz(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def need(x,msg):
 if not x:raise ValueError(msg)
def main():
 hand=json.loads(git(DATA,C,P+'handoff.json'));manifest=json.loads(git(DATA,C,P+'manifest.json'));files={'lineups':'actual-lineups-2025.jsonl.gz','raw_evidence':'raw-evidence.jsonl.gz','identity_candidates':'identity-candidates-2025.jsonl.gz','daily_identity':'pitcher-daily-identity.json','unconfirmed_review':'unconfirmed-review.json','manifest':'manifest.json'}
 payload={k:git(DATA,C,P+v) for k,v in files.items()}
 for key,data in payload.items():need(sha(data)==hand[key+'_sha256'],'handoff hash '+key)
 need(hand['contract_sha256']==manifest['contract_sha256']=='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480','contract hash')
 rows=readgz(payload['lineups']);candidates=readgz(payload['identity_candidates']);raw=readgz(payload['raw_evidence']);daily=json.loads(payload['daily_identity']);review=json.loads(payload['unconfirmed_review']);
 source=git(BASE,B,'experiments/kbo-player-data-20261103/rows.jsonl');batraw=git(BASE,B,'data/raw/detail/kbo_batters_indiv_2023_2026.json');pitraw=git(BASE,B,'data/raw/detail/kbo_baseball_2023_2026.json')
 need(sha(source)==manifest['source_rows_sha256'] and sha(batraw)==manifest['identity_archive_sha256'] and sha(pitraw)==manifest['pitcher_identity_archive_sha256'],'archive SHA')
 source_rows=[x for x in map(json.loads,source.splitlines()) if x['season']==2025];population={x['fixture_id']:x for x in source_rows};bat=json.loads(batraw);pit=json.loads(pitraw);candidate_map={x['fixture_id']:x for x in candidates}
 need(len(population)==len(rows)==len(candidate_map)==736 and len({x['fixture_id'] for x in rows})==736 and set(population)=={x['fixture_id'] for x in rows}==set(candidate_map),'cohort mismatch')
 identity_only(rows);identity_only(candidates)
 need(len({r['official_game_id'] for r in rows})==736,'duplicate official game mapping')
 bysha=collections.defaultdict(list);bodies={}
 for m in raw:
  if m.get('body_base64') is not None:
   b=base64.b64decode(m['body_base64'],validate=True);need(sha(b)==m['sha256'],'raw hash');
   if m['sha256'] in bodies:need(bodies[m['sha256']]==b,'raw hash collision')
   bodies[m['sha256']]=b;bysha[m['sha256']].append(m)
 counts=collections.Counter();used=set();excluded=[]
 def evidence(meta,endpoint):
  need(meta.get('http_status')==200 and meta.get('received_at') and meta.get('method')=='POST' and meta.get('url')=='https://www.koreabaseball.com/'+endpoint,'request metadata')
  b=bodies[meta['sha256']];need(any(x.get('url')==meta['url'] and x.get('params')==meta['params'] for x in bysha[meta['sha256']]),'missing request binding');used.add(meta['sha256']);return b
 @lru_cache(maxsize=None)
 def prof(key):return profile(bodies[key])
 def profile_check(key,pid):
  need(any(str((m.get('params') or {}).get('playerId'))==pid and '/Record/Player/' in m.get('url','') and m.get('http_status')==200 for m in bysha[key]),'profile request ID')
  used.add(key);return prof(key)
 @lru_cache(maxsize=None)
 def daily_table(key,pid):
  need(any(m.get('method')=='POST' and str((m.get('params') or {}).get('playerId'))==pid and m.get('url','').endswith('/PitcherDetail/Daily.aspx') for m in bysha[key]),'daily request ID')
  doc=BeautifulSoup(bodies[key].decode('utf8'),'html.parser')
  def chosen(s):
   x=doc.select_one('select[id$='+s+'] option[selected]');return x.get('value') if x else None
  need((chosen('ddlYear'),chosen('ddlSeries'))==('2025','0'),'daily period')
  out=[]
  for table in doc.find_all('table'):
   h=[x.get_text(' ',strip=True) for x in table.select('thead th')]
   if h.count('상대')!=1 or h.count('구분')!=1:continue
   for tr in table.select('tbody tr'):
    cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
    if len(cells)==len(h) and len(cells[0])==5 and cells[0][2]=='.':out.append((cells[0],cells[h.index('상대')],cells[h.index('구분')]))
  used.add(key);return out
 for r in rows:
  fid=r['fixture_id'];gid=r['official_game_id'];day=r['date'];src=population[fid];sm=r['schedule_evidence'];lm=r['lineup_evidence'];bm=r['box_evidence'];
  need(r['season']==2025 and r['historical_publication_time_proven'] is False and r['historical_receipt_proven'] is False and r['eligible_as_pregame_feature'] is False and r['actual_batting_starter_changes_verified'] is False,'availability '+fid)
  source_day=src['kickoff_at'][:10] if src.get('kickoff_at') else fid.split(':')[1];need(day==source_day and r['home']['team']['name']==src['home_id'] and r['away']['team']['name']==src['away_id'],'archived game identity '+fid)
  schedule=json.loads(evidence(sm,'ws/Main.asmx/GetKboGameList'));need(str(sm['params']['date'])==day.replace('-',''),'schedule request '+fid);g=[z for z in schedule['game'] if z.get('G_ID')==gid];need(len(g)==1,'schedule unique '+fid);g=g[0]
  need(str(g['G_DT'])==day.replace('-','') and int(g['LE_ID'])==1 and int(g['SEASON_ID'])==2025 and all(g[s.upper()+'_ID']==r[s]['team']['id'] and g[s.upper()+'_NM'].strip()==r[s]['team']['name'] for s in ('home','away')),'schedule metadata '+fid)
  if fid==gid+'2025':counts['exact_id']+=1
  elif fid.startswith('score:'):
   near=[z for z in schedule['game'] if z['G_DT']==g['G_DT'] and z['HOME_ID']==g['HOME_ID'] and z['AWAY_ID']==g['AWAY_ID']];need(len(near)==1,'score fixture ambiguous');counts['score_id']+=1
  else:need(fid[4:13]==gid[4:] and fid[-4:]=='2025','alias mismatch '+fid);counts['year_alias']+=1
  for m in (lm,bm):need(m['params']['gameId']==gid and int(m['params']['leId'])==1 and int(m['params']['seasonId'])==2025 and str(m['params']['srId'])==str(g['SR_ID']),'request binding '+fid)
  line=evidence(lm,'ws/Schedule.asmx/GetLineUpAnalysis');l=json.loads(line);flag=l[0][0]['LINEUP_CK'];box=json.loads(evidence(bm,'ws/Schedule.asmx/GetBoxScore'));need(str(box.get('code'))=='100' and len(box['tables'])==5,'box envelope '+fid)
  if flag is False:
   need(r['complete_verified'] is False and r['lineup_confirmed'] is False and set(r['exclusion_reasons'])=={'lineup_flag_false','complete_verified_lineup_unavailable'},'excluded reasons '+fid)
   for t in box['tables'][1:3]:
    need('선발' not in json.dumps(t,ensure_ascii=False),'explicit hitter starter found '+fid)
    for row in t.get('rows',[]):
     for cell in [row]+row.get('row',[]):need(not any(any(w in k.lower() for w in ('start','lineup','flag')) and v not in (None,'') for k,v in cell.items()),'hitter starter field '+fid)
   excluded.append(fid);counts['unconfirmed_excluded']+=1;order=None
  else:
   need(flag is True and r['complete_verified'] is True and r['lineup_confirmed'] is True and not r['exclusion_reasons'],'confirmed status '+fid);
   try:order=confirmed_lineups(line,gid,g['HOME_ID'],g['AWAY_ID'],2025,int(g['SR_ID']),allow_duplicate_names=True)['confirmed_lineups']
   except ValueError as exc:raise ValueError('lineup '+fid+' '+str(exc)) from exc
   counts['confirmed']+=1
  game=bat.get(fid) or bat.get(gid+'2025');pg=pit.get(fid) or pit.get(gid+'2025');ids=[];starter_ids=[]
  for side,bi,prefix in (('away',1,'T'),('home',2,'B')):
   if order is not None:
    players=r[side]['ordered_hitters'];need([(z['slot'],z['name']) for z in players]==order[g[side.upper()+'_ID']],'lineup mismatch '+fid)
    need(game and (game['date'],game['home'],game['away'])==(day,src['home_id'],src['away_id']),'batter archive identity '+fid)
    box_names={(int(z[0]),z[2]) for row in box['tables'][bi]['rows'] if len(z:=textrow(row))>=3 and z[0].isdigit()}
    for p in players:
     e=p['evidence'];pid=p['player_id'];ids.append(pid);need(not p['exclusion_reasons'] and (p['slot'],p['name']) in box_names and e['lineup_sha256']==lm['sha256'] and e['box_sha256']==bm['sha256'],'hitter evidence '+fid)
     expected=sorted({str(z['code']) for z in game['data'][side] if z.get('order')==p['slot'] and str(z.get('code','')).isdigit()});checks=e['candidate_profile_checks'];need(expected==e['candidate_player_ids'] and sorted(q['player_id'] for q in checks)==expected,'candidate coverage '+fid)
     matches=[]
     for q in checks:
      name=profile_check(q['sha256'],q['player_id']);need(name==q['name'],'profile evidence '+fid)
      if name==p['name']:matches.append(q['player_id'])
     need(matches==[pid] and e['profile_player_id']==pid,'ambiguous hitter '+fid);counts['hitter_slots']+=1
   pitch=box['tables'][3 if side=='away' else 4];heads=textrow(pitch['headers'][0]);need(heads.count('선수명')==heads.count('등판')==1,'pitch headers '+fid);ni=heads.index('선수명');ai=heads.index('등판');pr=[textrow(z) for z in pitch['rows']];starts=[z for z in pr if z[ai]=='선발'];need(len(starts)==1,'actual pitcher flag '+fid)
   p=r[side]['starter'];pid=p['player_id'];starter_ids.append(pid);claim=starts[0][ni];need(p['actual_record_verified'] is True and p['actual_record_starter_cell']==claim and str(g[prefix+'_PIT_P_ID'])==pid and p['schedule_agrees'] is True,'pitch source '+fid)
   if fid in daily and side=='away':
    d=daily[fid];need(d['player_id']==pid=='51454' and d['official_game_id']==gid and d['box_sha256']==bm['sha256'] and d['schedule_sha256']==sm['sha256'] and p['identity_candidate_ids']==['51454','60146'],'daily roster '+fid)
    need(pg and (pg['date'],pg['home'],pg['away'])==(day,src['home_id'],src['away_id']),'daily same-game pitcher archive '+fid);same={str(z['pcode']) for z in pg['data'][side] if str(z.get('name') or '').strip()=='이승현'};need(same=={'51454','60146'} and sum(z[ni]=='이승현' for z in pr)==2,'daily candidates '+fid)
    for q in d['daily_identity_checks']:
     ident=q['player_id'];prf=q['profile'];need(profile_check(prf['sha256'],ident)=='이승현','daily profile '+fid);role='선발' if ident=='51454' else '구원';same_day=[x for x in daily_table(q['daily_sha256'],ident) if x[:2]==(day[5:7]+'.'+day[8:10],src['home_id'])];need(same_day==[(day[5:7]+'.'+day[8:10],src['home_id'],role)] and q['role']==role,'daily role '+fid)
    need(len(d['daily_identity_checks'])==2,'daily candidate count '+fid);counts['daily_disambiguated']+=1
   else:
    if claim.isdigit():need(claim==pid,'numeric starter '+fid)
    else:need(claim==p['name'] and sum(z[ni]==claim for z in pr)==1 and profile_check(p['profile_sha256'],pid)==claim,'named starter '+fid)
   counts['starter_sides']+=1
  if order is not None:need(len(ids)==len(set(ids))==18,'duplicate hitter '+fid)
  need(len(set(starter_ids))==2,'duplicate starters '+fid);counts['fixtures']+=1;counts['kickoff_mismatch']+=int(r['source_kickoff_matches_official'] is False)
 need(set(excluded)=={x['fixture_id'] for x in review} and len(excluded)==5 and counts['daily_disambiguated']==len(daily)==5 and counts['confirmed']==731,'exclusion/daily counts')
 print(json.dumps({'commit':C,'counts':counts,'excluded':sorted(excluded),'raw_records':len(raw),'unique_raw_bodies':len(bodies),'referenced_bodies_checked':len(used),'historical_receipt_proven':False,'model_performance_calculated':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
