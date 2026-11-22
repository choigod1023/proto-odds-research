"""Finalize the original 709-game 2026 actual-starter verification evidence."""
import base64,collections,json,os,subprocess
from functools import lru_cache
from pathlib import Path
import collect_actual_starters as p
import collect_lineups as c
from build_rosters_2023 import SOURCE,CACHE,FROZEN,PIT,fixed_file,gzwrite,sha
HERE=Path(__file__).resolve().parent
OFFICIAL=Path(r'C:\Users\user\Documents\Codex\klu\experiments\kbo-official-lineups-20261103\official-lineups-2026.jsonl')
KL=Path(r'C:\Users\user\Documents\Codex\klu');KL_COMMIT='b24739cf52435070586f8687011fd473a16123e4'
GROUP=SOURCE/'experiments/kbo-player-data-20261103/official-full-2026-cohorts.jsonl'

def main():
 raw=OFFICIAL.read_bytes();blob=subprocess.check_output(['git','cat-file','blob',KL_COMMIT+':'+OFFICIAL.relative_to(KL).as_posix()],cwd=KL)
 if raw!=blob or sha(raw)!='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3':raise ValueError('official_2026_source_mismatch')
 official={x['fixture_id']:x for x in map(json.loads,raw.splitlines())}
 ids=json.loads(fixed_file(GROUP))['all']
 if len(ids)!=709 or len(set(ids))!=709:raise ValueError('cohort_709_mismatch')
 captured=[json.loads(x) for x in (CACHE/'actual-starters-2026-final.jsonl').read_text(encoding='utf8').splitlines()]
 if len(captured)!=709 or len({x['fixture_id'] for x in captured})!=709:raise ValueError('actual_starter_capture_incomplete')
 captured={x['fixture_id']:x for x in captured}
 if set(captured)!=set(ids):raise ValueError('actual_starter_cohort_mismatch')
 archive=json.loads(fixed_file(PIT,'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'))
 raws={};output=[];counts=collections.Counter();profile_name=lru_cache(8192)(c.profile_name)
 def response(meta):
  b=p.evidence(CACHE,meta);raws[meta['sha256']]=b;return b
 for fid in ids:
  o=official[fid];x=captured[fid];reason=None;actual=None
  try:
   if x.get('failure_evidence'):
    failure=x['failure_evidence'];failed=(CACHE/'raw'/failure['sha256']).read_bytes()
    if sha(failed)!=failure['sha256'] or len(failed)!=failure['bytes']:raise ValueError('failure_raw_sha_mismatch')
    raws[failure['sha256']]=failed
   if x.get('schedule_evidence'):response(x['schedule_evidence'])
   if x.get('actual_pitcher_record_evidence'):response(x['actual_pitcher_record_evidence'])
   if x['status']!='ok':raise ValueError(x.get('reason','actual_record_unavailable'))
   bm=x['actual_pitcher_record_evidence'];params=bm.get('params') or {}
   if bm.get('url')!=p.BOX or bm.get('method')!='POST' or any(str(params.get(k))!=str(v) for k,v in [('gameId',o['official_game_id']),('leId',1),('seasonId',2026),('srId',o['series_id'])]):raise ValueError('starter_request_identity_mismatch')
   schedule=json.loads(response(x['schedule_evidence']));matches=[g for g in schedule['game'] if g.get('G_ID')==o['official_game_id']]
   if len(matches)!=1:raise ValueError('schedule_ambiguous')
   game=matches[0]
   for key,want in [('G_ID',o['official_game_id']),('G_DT',o['game_date']),('SEASON_ID',2026),('LE_ID',1),('SR_ID',o['series_id']),('HEADER_NO',o['header_no']),('HOME_ID',o['home_team_id']),('AWAY_ID',o['away_team_id'])]:
    if str(game.get(key))!=str(want):raise ValueError('schedule_identity_mismatch_'+key)
   def resolve(side,claim):
    item=x['starters'][side];pid=item['player_id'];ev=item['evidence']['profile'];name=profile_name(response(ev))
    if str(ev['params']['playerId'])!=pid or name!=item['name']:raise ValueError('pitcher_profile_mismatch')
    if claim.isdigit():
     if pid!=claim:raise ValueError('pitcher_direct_id_mismatch')
    else:
     candidates={str(a['pcode']) for a in ((archive.get(fid) or {}).get('data') or {}).get(side,[]) if str(a.get('name') or '').strip()==claim and str(a.get('pcode') or '').isdigit()}
     if candidates!={pid}:
      scheduled=(o.get('starting_pitchers') or {}).get(side) or {};sname=str(scheduled.get('name') or '').strip();sid=str(scheduled.get('player_id') or '')
      candidates={str(a['pcode']) for a in ((archive.get(fid) or {}).get('data') or {}).get(side,[]) if str(a.get('name') or '').strip()==sname and str(a.get('pcode') or '').isdigit()}
      if sid!=pid or candidates!={pid}:raise ValueError('pitcher_name_ambiguous')
     if name!=claim:raise ValueError('pitcher_profile_name_mismatch')
    return pid,name,ev
   actual=p.parse_box(json.loads(response(x['actual_pitcher_record_evidence'])),game,o,resolve)
   if actual!=x['starters']:raise ValueError('pitcher_recheck_mismatch')
   for side in ('home','away'):
    actual[side]['evidence']['official_record']=x['actual_pitcher_record_evidence'];actual[side]['evidence']['schedule']=x['schedule_evidence']
    counts['schedule_disagrees_'+side]+=int(not actual[side]['evidence']['schedule_matches_actual'])
  except (ValueError,KeyError,TypeError,IndexError) as exc:reason=str(exc);counts['excluded_'+reason]+=1
  counts['complete' if reason is None else 'excluded']+=1
  output.append({'fixture_id':fid,'season':2026,'official_game_id':o['official_game_id'],'date':o['game_date'],'teams':{'home':o['home_team_id'],'away':o['away_team_id']},'status':'complete' if reason is None else 'excluded','starters':actual,'exclusions':[] if reason is None else [reason],'source_evidence':{'schedule':x.get('schedule_evidence'),'actual_pitcher_record':x.get('actual_pitcher_record_evidence'),'failure':x.get('failure_evidence')},'historical_receipt_proven':False})
 f=HERE/'actual-starters-2026.jsonl.gz';e=HERE/'actual-starters-2026-raw.jsonl.gz';files={'records':gzwrite(f,output),'raw':gzwrite(e,({'sha256':k,'bytes':len(v),'raw_base64':base64.b64encode(v).decode('ascii')} for k,v in sorted(raws.items())))}
 m={'status':'finalized_2026_starter_verification','population':709,'counts':dict(counts),'sha256':files,'raw_response_count':len(raws),'source_official_commit':KL_COMMIT,'source_cohort_commit':FROZEN,'historical_receipt_proven':0};tmp=HERE/'actual-starters-2026-manifest.json.tmp';tmp.write_text(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');os.replace(tmp,HERE/'actual-starters-2026-manifest.json');print(json.dumps(m))
if __name__=='__main__':main()
