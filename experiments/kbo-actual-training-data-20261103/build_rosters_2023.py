"""Publish the finished 2023 identity-only official rosters and their raw evidence."""
import base64,collections,gzip,hashlib,json,os,subprocess
from datetime import datetime
from pathlib import Path
from functools import lru_cache
import collect_lineups as c
import collect_actual_starters as p

HERE=Path(__file__).resolve().parent
SOURCE=Path(r'C:\Users\user\Documents\Codex\kbodata')
FROZEN='13291d23e855e0d500327cb39ac64a480e395837'
CACHE=Path('.research-state/kbo-actual-2023')
ROWS=SOURCE/'experiments/kbo-player-data-20261103/rows.jsonl'
BAT=SOURCE/'data/raw/detail/kbo_batters_indiv_2023_2026.json'
PIT=SOURCE/'data/raw/detail/kbo_baseball_2023_2026.json'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def fixed_file(path,expected=None):
 raw=path.read_bytes();rel=path.relative_to(SOURCE).as_posix();blob=subprocess.check_output(['git','cat-file','blob',FROZEN+':'+rel],cwd=SOURCE)
 if raw!=blob or (expected and sha(raw)!=expected):raise ValueError('source_git_bytes_mismatch:'+rel)
 return raw

def gzwrite(path,rows):
 tmp=path.with_name(path.name+'.tmp')
 with tmp.open('wb') as fp:
  with gzip.GzipFile(fileobj=fp,mode='wb',filename='',mtime=0) as f:
   for r in rows:f.write((json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf8'))
 os.replace(tmp,path);return sha(path.read_bytes())

def main():
 rows=[json.loads(x) for x in fixed_file(ROWS,'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c').splitlines()];rows={x['fixture_id']:x for x in rows if x['season']==2023}
 bat=json.loads(fixed_file(BAT,'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'))
 pitch_archive=json.loads(fixed_file(PIT,'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'))
 if len(rows)!=723:raise ValueError('2023_cohort_not_723')
 line=[json.loads(x) for x in (CACHE/'official-lineups.jsonl').read_text(encoding='utf8').splitlines()];line={x['fixture_id']:x for x in line if x['fixture_id'] in rows}
 starters=[json.loads(x) for x in (CACHE/'actual-starters-2023.jsonl').read_text(encoding='utf8').splitlines()]
 if len(starters)!=len({x['fixture_id'] for x in starters}):raise ValueError('starter_duplicate')
 starters={x['fixture_id']:x for x in starters}
 if set(rows)!=set(line) or set(rows)!=set(starters):raise ValueError('capture_not_finished')
 get=p.evidence;raws={};results=[];stats=collections.Counter()
 def raw(meta):
  x=get(CACHE,meta);raws[meta['sha256']]=x;return x
 profile_name=lru_cache(maxsize=8192)(c.profile_name)
 for fid,row in sorted(rows.items(),key=lambda x:(x[1]['kickoff_at'] or '',x[0])):
  rec=line[fid];st=starters[fid];reasons=[];hitters=None;pitchers=None;schedule=None
  try:
   sm=rec['schedule_evidence'];schedule=json.loads(raw(sm));matching=[x for x in schedule['game'] if x.get('G_ID')==rec['official_game_id']]
   if len(matching)!=1:raise ValueError('schedule_ambiguous')
   game=matching[0]
   for k,v in [('G_ID',rec['official_game_id']),('G_DT',rec['game_date']),('SEASON_ID',2023),('LE_ID',1),('SR_ID',rec['series_id']),('HEADER_NO',rec['header_no']),('HOME_ID',rec['home_team_id']),('AWAY_ID',rec['away_team_id'])]:
    if str(game.get(k))!=str(v):raise ValueError('schedule_'+k)
   if fid!=game['G_ID']+'2023' or (game['HOME_NM'].strip(),game['AWAY_NM'].strip())!=(row['home_id'],row['away_id']):raise ValueError('fixture_team_crosswalk')
   if rec.get('lineup_status')!='official_flag_true_profile_name_crossmatched':raise ValueError('lineup_'+str(rec.get('lineup_status')))
   lm=rec['lineup_evidence'];official=c.parse_lineup(raw(lm),game)
   if official is None:raise ValueError('lineup_flag_not_true')
   hitters={};seen=set()
   for side in ('home','away'):
    chosen=sorted(rec['lineups'][side],key=lambda x:x['order']);items=[]
    if len(chosen)!=9:raise ValueError('lineup_length')
    for chosen_p,parsed in zip(chosen,official[side]):
     if (chosen_p['order'],chosen_p['name'])!=(parsed['order'],parsed['name']):raise ValueError('lineup_raw_mismatch')
     pid=chosen_p['player_id'];ev=chosen_p['official_profile_evidence']
     if not pid.isdigit() or pid in seen or str(ev['params']['playerId'])!=pid or profile_name(raw(ev))!=parsed['name']:raise ValueError('lineup_id_profile_mismatch')
     archive=bat.get(fid);candidates=[] if not archive else [x for x in archive['data'][side] if str(x.get('code'))==pid and x.get('order')==parsed['order']]
     if not candidates:raise ValueError('lineup_identity_not_backed_by_same_game_order')
     seen.add(pid);items.append({'slot':parsed['order'],'player_id':pid,'name':parsed['name'],'evidence':{'basis':chosen_p['player_id_basis'],'lineup':lm,'profile':ev,'archive_fixture_id':fid,'archive_identity_only':True}})
    hitters[side]=items
  except (ValueError,KeyError,TypeError,IndexError) as exc:reasons.append('lineup:'+str(exc))
  try:
   if st['status']!='ok':raise ValueError(st.get('reason','starter_capture_unavailable'))
   if schedule is None:raise ValueError('schedule_unavailable')
   def resolve(side,claim):
    saved=st['starters'][side];pid=saved['player_id'];ev=saved['evidence']['profile'];name=profile_name(raw(ev))
    if str(ev['params']['playerId'])!=pid or name!=saved['name']:raise ValueError('starter_profile_mismatch')
    if claim.isdigit():
     if claim!=pid:raise ValueError('starter_numeric_claim_mismatch')
    else:
     candidates={str(x['pcode']) for x in ((pitch_archive.get(fid) or {}).get('data') or {}).get(side,[]) if str(x.get('name') or '').strip()==claim and str(x.get('pcode') or '').isdigit()}
     if candidates!={pid} or name!=claim:raise ValueError('starter_name_mapping_ambiguous')
    return pid,name,ev
   actual=p.parse_box(json.loads(raw(st['actual_pitcher_record_evidence'])),game,rec,resolve)
   if actual!=st['starters']:raise ValueError('starter_recheck_mismatch')
   pitchers={side:{**actual[side],'evidence':{**actual[side]['evidence'],'official_record':st['actual_pitcher_record_evidence'],'schedule':st['schedule_evidence']}} for side in ('home','away')}
  except (ValueError,KeyError,TypeError,IndexError) as exc:reasons.append('starter:'+str(exc))
  stats['lineup_complete']+=int(hitters is not None);stats['starter_complete']+=int(pitchers is not None);stats['actual_complete']+=int(not reasons)
  for reason in reasons:stats['excluded_'+reason]+=1
  results.append({'fixture_id':fid,'season':2023,'date':row['kickoff_at'][:10] if row.get('kickoff_at') else fid[:4]+'-'+fid[4:6]+'-'+fid[6:8],'official_game_id':rec.get('official_game_id'),'teams':{'home':row['home_id'],'away':row['away_id']},'hitters':hitters,'starters':pitchers,'status':'complete' if not reasons else 'excluded','exclusions':reasons,'source_kind':'official_retrospective_identity_only','historical_publication_time_proven':False,'historical_receipt_proven':False,'target_player_stats_used_as_features':False})
 out=HERE/'actual-rosters-2023.jsonl.gz';rawout=HERE/'actual-rosters-2023-raw.jsonl.gz';outsha=gzwrite(out,results);rawsha=gzwrite(rawout,({'sha256':k,'bytes':len(v),'raw_base64':base64.b64encode(v).decode('ascii')} for k,v in sorted(raws.items())))
 m={'status':'finalized_2023_source_only','population':723,'counts':dict(stats),'rows_sha256':outsha,'raw_evidence_bundle_sha256':rawsha,'source_rows_commit':FROZEN,'official_raw_response_count':len(raws),'historical_receipt_proven':0,'historical_publication_time_proven':0,'limitations':['Official hitter IDs crossmatched with same-game team/order identity and current official profile, never target scores/statistics.','Pitcher must be an official unique first starting-flag row matching official schedule team and ID/name.','Official records and identity verification are retrospective; historical announcement and receipt were not proven.','No downstream training/evaluation performed.']}
 tmp=HERE/'actual-rosters-2023-manifest.json.tmp';tmp.write_text(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');os.replace(tmp,HERE/'actual-rosters-2023-manifest.json');print(json.dumps(m,ensure_ascii=False))
if __name__=='__main__':main()
