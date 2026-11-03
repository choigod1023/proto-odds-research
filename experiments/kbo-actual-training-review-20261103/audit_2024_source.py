"""Audit pinned 2024 official roster evidence without network or model fitting."""
import collections,gzip,hashlib,io,json,re,subprocess,tarfile
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from official_parse import confirmed_lineups
from audit_2023_source import profile,textrow
DATA=Path(r'C:\Users\user\Documents\Codex\kbt24');BASE=Path(r'C:\Users\user\Documents\Codex\kbodata');C='e8ba21f970f73594848e75cc6515a1dd3a994bf1';B='13291d23e855e0d500327cb39ac64a480e395837';P='experiments/kbo-actual-2024-20261103/'
def blob(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(x):return hashlib.sha256(x).hexdigest()
def need(x,why):
 if not x:raise ValueError(why)
def identity_only(value):
 if isinstance(value,dict):
  need(not (set(value)&{'bf','hit','hr','rbi','era','ab','score','pitch_count','runs','outcome','odds'}),'target statistic in identity output')
  for x in value.values():identity_only(x)
 elif isinstance(value,list):
  for x in value:identity_only(x)
def main():
 names={'rosters.jsonl':'2c56d0139dd6de89777aefe9e74d3e925af5bb9dafc3d4603c68f44fd640d485','batter-identity-only.json':'7aafb72ca2df318bf3642b7f5ddcc59fbd09013cdc3a70b5ff672b33c2f4254f','official-evidence.tar.gz':'512bed8407fca4132d2497e2dcc7b1ee43cd92133d7ffbe1e65d4ff4eae88800'}
 payload={k:blob(DATA,C,P+k) for k in names}
 for k,v in names.items():need(sha(payload[k])==v,'artifact hash '+k)
 manifest=json.loads(blob(DATA,C,P+'manifest.json'));need(manifest['contract_sha256']=='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480','contract hash')
 source=blob(BASE,B,'experiments/kbo-player-data-20261103/rows.jsonl');batraw=blob(BASE,B,'data/raw/detail/kbo_batters_indiv_2023_2026.json');pitraw=blob(BASE,B,'data/raw/detail/kbo_baseball_2023_2026.json')
 for raw,key in ((source,'input_sha256'),(batraw,'batter_source_sha256'),(pitraw,'pitcher_identity_source_sha256')):need(sha(raw)==manifest[key],'archive digest '+key)
 bat_sha=sha(batraw);pit_sha=sha(pitraw)
 population={r['fixture_id']:r for r in map(json.loads,source.splitlines()) if r['season']==2024};roster=list(map(json.loads,payload['rosters.jsonl'].splitlines()));identity=json.loads(payload['batter-identity-only.json']);bat=json.loads(batraw);pitch=json.loads(pitraw)
 identity_only(roster);identity_only(identity)
 need(len(population)==len(roster)==728 and len({r['fixture_id'] for r in roster})==728 and set(population)=={r['fixture_id'] for r in roster},'population mismatch')
 need(len({r['official_game_id'] for r in roster})==728,'official IDs not unique')
 with tarfile.open(fileobj=io.BytesIO(payload['official-evidence.tar.gz']),mode='r:gz') as tf:
  raws={};reqs=[];attempts=[]
  for m in tf.getmembers():
   if m.isfile() and m.name.startswith('raw/'):
    b=gzip.decompress(tf.extractfile(m).read());key=Path(m.name).name.removesuffix('.gz');need(key not in raws and sha(b)==key,'raw hash '+key);raws[key]=b
   elif m.isfile() and m.name=='attempts.jsonl':
    attempts=[json.loads(x) for x in tf.extractfile(m).read().splitlines()]
   elif m.isfile() and m.name.startswith('requests/'):
    reqs.append(json.load(tf.extractfile(m)))
 starts=sorted(datetime.fromisoformat(x['started_at']) for x in attempts);intervals=[(b-a).total_seconds() for a,b in zip(starts,starts[1:])];need(len(attempts)==manifest['new_direct_requests'] and sum(x<1.5 for x in intervals)==manifest['request_spacing_violations_under_1_5_seconds'],'request log')
 archived_games=[json.loads(x) for x in gzip.decompress(blob(BASE,B,'data/model_candidates/kbo/archived-games.jsonl.gz')).splitlines()]
 resumed_history=[{'id':x['source_fixture_id'],'date':x['date']} for x in archived_games if x['date'] in ('2024-10-21','2024-10-23') and {x['home'],x['away']}=={'KIA','삼성'}]
 need(resumed_history==[{'id':'77771023SSHT02024','date':'2024-10-23'}],'unexpected suspended-game archive history')
 used=set();counts=collections.Counter();resumption={};profile_options=collections.defaultdict(list);ambiguity_checks=[]
 for m in reqs:
  if '/Record/Player/HitterDetail/' in m.get('url','') and m.get('http_status')==200:
   pid=str((m.get('params') or m.get('query') or {}).get('playerId') or '')
   if pid.isdigit() and m.get('sha256') in raws:profile_options[pid].append(m)
 def evidence(meta,endpoint=None):
  need(meta['http_status']==200 and meta['received_at'] and (endpoint is None or meta['url']=='https://www.koreabaseball.com/'+endpoint),'request metadata')
  b=raws[meta['sha256']];need(len(b)==meta['bytes'],'response bytes');used.add(meta['sha256']);return b
 for r in roster:
  fid=r['fixture_id'];gid=r['official_game_id'];day=r['date'];ref=population[fid];ev=r['evidence'];sm=ev['schedule_evidence'];lm=ev['lineup_evidence'];bm=ev['boxscore_evidence']
  need(r['season']==2024 and not r['exclusion_reasons'] and r['eligible_as_pregame_feature'] is False and r['historical_publication_time'] is None and r['historical_receipt_time'] is None,'status '+fid)
  need((ref['kickoff_at'][:10] if ref['kickoff_at'] else fid.split(':')[1])==day and ref['home_id']==r['home_team'] and ref['away_id']==r['away_team'],'source match '+fid)
  schedule=json.loads(evidence(sm,'ws/Main.asmx/GetKboGameList'));need(str(sm['params']['date'])==day.replace('-',''),'schedule request '+fid)
  matches=[g for g in schedule['game'] if g['G_ID']==gid];need(len(matches)==1,'game ID '+fid);g=matches[0]
  need(g['G_DT']==day.replace('-','') and int(g['SEASON_ID'])==2024 and int(g['LE_ID'])==1 and str(g['SR_ID'])==str(r['official_series_id']) and g['HOME_NM'].strip()==r['home_team'] and g['AWAY_NM'].strip()==r['away_team'] and g['HOME_ID']==r['official_home_team_id'] and g['AWAY_ID']==r['official_away_team_id'],'game metadata '+fid)
  if fid==gid+'2024':counts['exact_game_id']+=1
  elif fid.startswith('score:'):
   same=[z for z in schedule['game'] if z['G_DT']==g['G_DT'] and z['HOME_ID']==g['HOME_ID'] and z['AWAY_ID']==g['AWAY_ID']];need(len(same)==1,'ambiguous score fixture '+fid);counts['unique_date_team']+=1
  else:
   need(fid[4:13]==gid[4:] and fid[-4:]=='2024','alias mismatch '+fid);counts['synthetic_year_alias']+=1
  for meta in (lm,bm):
   params=meta['params'];need(params['gameId']==gid and int(params['seasonId'])==2024 and int(params['leId'])==1 and str(params['srId'])==str(g['SR_ID']),'request game binding '+fid)
  lineup=evidence(lm,'ws/Schedule.asmx/GetLineUpAnalysis');box=json.loads(evidence(bm,'ws/Schedule.asmx/GetBoxScoreScroll'))
  order=confirmed_lineups(lineup,gid,g['HOME_ID'],g['AWAY_ID'],2024,int(g['SR_ID']))['confirmed_lineups'];need(str(box.get('code'))=='100' and len(box['arrPitcher'])==2,'box envelope '+fid)
  key=r['batter_identity_source_key'];rawgame=bat.get(key);need(rawgame is not None and (rawgame['date'],rawgame['home'],rawgame['away'])==(day,r['home_team'],r['away_team']),'same-game archive '+fid)
  ids=[];starters=[]
  for side,bi,prefix in (('away',0,'T'),('home',1,'B')):
   team=g[side.upper()+'_ID'];players=r[side]['ordered_hitters'];need([(z['slot'],z['name']) for z in players]==order[team],'official confirmed lineup '+fid)
   archived=rawgame['data'][side];extract=identity[gid][side];need(extract==[{k:z.get(k) for k in ('code','name','order')} for z in archived],'identity extract differs from source '+fid)
   table=json.loads(box['arrHitter'][bi]['table1']);first={}
   for row in table['rows']:
    z=textrow(row)
    if len(z)>=3 and z[0].isdigit():first.setdefault(int(z[0]),z[2])
   for z in players:
    pid=z['player_id'];pe=z['evidence']['official_profile'];ids.append(pid)
    need(pid.isdigit() and z['evidence']['identity_basis'] in ('same_game_side_order_name','same_game_side_order_profile_name_required') and z['evidence']['same_game_batter_source_sha256']==bat_sha,'hitter identity metadata '+fid)
    need(first.get(z['slot'])==z['name'] and any(str(q['code'])==pid and q['order']==z['slot'] and (z['evidence']['identity_basis']=='same_game_side_order_profile_name_required' or str(q['name']).strip()==z['name']) for q in extract),'box and source crossmatch '+fid)
    counts['hitter_profile_name_required']+=int(z['evidence']['identity_basis']=='same_game_side_order_profile_name_required')
    req=pe.get('params') or pe.get('query') or {};need(str(req.get('playerId'))==pid and profile(evidence(pe))==z['name'],'official player profile '+fid);profile_options[pid].append(pe)
    same=[q for q in extract if q['order']==z['slot'] and str(q['code']).isdigit()];exact={str(q['code']) for q in same if str(q['name']).strip()==z['name']};candidate=exact or {str(q['code']) for q in same};ambiguity_checks.append((fid,z['slot'],z['name'],pid,candidate));counts['hitter_slots']+=1
   table=json.loads(box['arrPitcher'][bi]['table']);header=textrow(table['headers'][0]);need(header.count('선수명')==header.count('등판')==1,'pitch header '+fid);ni=header.index('선수명');ai=header.index('등판');starts=[textrow(x) for x in table['rows'] if textrow(x)[ai]=='선발'];need(len(starts)==1,'pitch flag '+fid)
   p=r[side]['starter'];pid=p['player_id'];starters.append(pid);need(p['actual_record_verified'] is True and starts[0][ni]==p['name'] and str(g[prefix+'_PIT_P_ID'])==pid,'pitch identity '+fid)
   scheduled=str(g[prefix+'_PIT_P_NM']).strip()
   if scheduled!=p['name']:
    a=p['name_alias_evidence'];need((pid,scheduled,p['name'])==('68419','최채흥','최지명') and a['same_game_pitcher_source_sha256']==pit_sha,'pitch alias '+fid)
    old=pitch[a['same_game_source_key']];need((old['date'],old['home'],old['away'])==(day,r['home_team'],r['away_team']) and sum(str(z.get('pcode'))==pid and z.get('name')==scheduled for z in old['data'][side])==1,'pitch alias archive '+fid)
    e=a['official_current_profile'];req=e.get('params') or e.get('query') or {};need(str(req.get('playerId'))==pid and profile(evidence(e))==p['name'],'pitch alias profile '+fid);counts['pitcher_renamed_sides']+=1
   counts['starters']+=1
  need(len(ids)==len(set(ids))==18 and len(set(starters))==2,'duplicate identity '+fid)
  if day=='2024-10-23' and g['HOME_ID']=='HT' and g['AWAY_ID']=='SS':
   nearby=[{'id':z['G_ID'],'date':z['G_DT'],'header':z['HEADER_NO'],'series':z['SR_ID']} for z in schedule['game'] if z['HOME_ID']=='HT' and z['AWAY_ID']=='SS'];need({z['id'] for z in nearby}=={'20241021SSHT0','20241023SSHT0'} and gid=='20241023SSHT0' and key=='77771023SSHT02024','resumed game confusion');resumption={'selected':gid,'same_response':nearby,'selected_source_date':rawgame['date'],'fixture_id':fid}
  counts['fixtures']+=1
 @lru_cache(maxsize=None)
 def profile_sha(key):return profile(raws[key])
 for fid,slot,name,selected,candidates in ambiguity_checks:
  matches=set();unknown=[]
  for pid in candidates:
   recognized=set()
   for meta in profile_options[pid]:
    try:recognized.add(profile_sha(meta['sha256']))
    except (ValueError,UnicodeDecodeError):pass
   if not recognized:unknown.append(pid)
   elif name in recognized:matches.add(pid)
  if matches!={selected}:raise ValueError('ambiguous player profile '+fid+' slot '+str(slot))
  if unknown:counts['candidate_profiles_unavailable']+=1
 need(counts['candidate_profiles_unavailable']==0,'unverifiable alternate player identity')
 need(manifest['eligible']==728 and manifest['population']==728 and len(used)==1898,'coverage mismatch')
 print(json.dumps({'commit':C,'counts':counts,'all_bundled_raw_verified':len(raws),'referenced_bodies_verified':len(used),'request_records':len(reqs),'request_attempts':len(attempts),'minimum_start_interval_seconds':min(intervals),'resumption':resumption,'archived_history_for_resumption':resumed_history,'excluded':0,'historical_receipt_proven':False,'request_spacing_violations_reported':manifest['request_spacing_violations_under_1_5_seconds'],'model_performance_calculated':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
