"""Capture official boxscore starter evidence; 2023 and 2026, no features or labels."""
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import collect_lineups as lineups

ROOT=Path(__file__).resolve().parent
CACHE=Path('.research-state/kbo-actual-2023')
BOX=lineups.ROOT+'/ws/Schedule.asmx/GetBoxScore'
ROWS=Path(r'C:\Users\user\Documents\Codex\kbodata\experiments\kbo-player-data-20261103\rows.jsonl')
GAP=Path(r'C:\Users\user\Documents\Codex\kbor\experiments\kbo-official-record-gap-20261103\artifacts\normalized-records.jsonl.gz')

def sha(b):return hashlib.sha256(b).hexdigest()
def evidence(cache,meta):
 if not isinstance(meta,dict) or meta.get('http_status')!=200:raise ValueError('bad_source_evidence')
 if not str(meta.get('url') or '').startswith('https://www.koreabaseball.com/'):raise ValueError('nonofficial_source_url')
 try:received=datetime.fromisoformat(meta['received_at'].replace('Z','+00:00'))
 except (KeyError,TypeError,ValueError) as exc:raise ValueError('bad_source_receipt') from exc
 if received.utcoffset() is None:raise ValueError('source_receipt_timezone_missing')
 p=cache/'raw'/meta['sha256'];raw=p.read_bytes()
 if sha(raw)!=meta['sha256'] or len(raw)!=meta['bytes']:raise ValueError('bad_source_bytes')
 return raw

def parse_box(body,schedule,source,resolver=None):
 if str(body.get('code'))!='100' or not isinstance(body.get('tables'),list) or len(body['tables'])!=5:raise ValueError('invalid_box_envelope')
 if str(schedule.get('GAME_STATE_SC'))!='3' or str(schedule.get('GAME_RESULT_CK'))!='1':raise ValueError('schedule_not_completed')
 ret={};seen=set()
 for side,i,prefix in [('away',3,'T'),('home',4,'B')]:
  table=body['tables'][i];headers=[str(x.get('Text') or '').strip() for x in table['headers'][0]['row']]
  if headers.count('선수명')!=1 or headers.count('등판')!=1:raise ValueError('pitcher_header_mismatch')
  ni=headers.index('선수명');ai=headers.index('등판');starts=[]
  for row in table.get('rows',[]):
   values=[str(x.get('Text') or '').strip() for x in row['row']]
   if len(values)!=len(headers):raise ValueError('pitcher_row_shape_mismatch')
   if values[ai]=='선발':starts.append(values)
  if len(starts)!=1:raise ValueError(side+'_starter_flag_missing_or_ambiguous')
  if table['rows'][0]['row'][ai]['Text'].strip()!='선발':raise ValueError(side+'_starter_not_first')
  raw_id=starts[0][ni];expected_id=str(schedule.get(prefix+'_PIT_P_ID') or '');expected_name=str(schedule.get(prefix+'_PIT_P_NM') or '').strip()
  if not expected_id.isdigit() or not expected_name:raise ValueError(side+'_schedule_starter_missing')
  recorded=(source.get('starting_pitchers') or {}).get(side) or {}
  if str(recorded.get('player_id') or '')!=expected_id or str(recorded.get('name') or '').strip()!=expected_name:raise ValueError(side+'_cached_schedule_starter_mismatch')
  if raw_id in (expected_id,expected_name):
   actual_id,actual_name=expected_id,expected_name
   ev={'basis':'official_completed_boxscore_unique_first_starting_flag_and_official_schedule_same_team_name_or_id','schedule_matches_actual':True}
  else:
   if resolver is None:raise ValueError(side+'_official_box_schedule_starter_mismatch')
   actual_id,actual_name,profile=resolver(side,raw_id)
   ev={'basis':'official_completed_boxscore_unique_first_starting_flag_and_official_pitcher_profile','schedule_matches_actual':False,'schedule_player_id':expected_id,'schedule_name':expected_name,'profile':profile}
  if actual_id in seen:raise ValueError('starter_id_duplicate')
  seen.add(actual_id)
  ev.update(box_row_player_field=raw_id,box_table_index=i,schedule_team_id=str(schedule[side.upper()+'_ID']))
  ret[side]={'player_id':actual_id,'name':actual_name,'actual_record_verified':True,'evidence':ev}
 return ret

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--season',required=True,type=int,choices=(2023,2026));ap.add_argument('--max-requests',required=True,type=int);ap.add_argument('--cached-only',action='store_true');args=ap.parse_args()
 if args.max_requests<0:raise ValueError('negative request budget')
 if args.season==2026:
  official=Path(r'C:\Users\user\Documents\Codex\klu\experiments\kbo-official-lineups-20261103\official-lineups-2026.jsonl');raw=official.read_bytes()
  if sha(raw)!='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3':raise ValueError('2026_official_frozen_source_mismatch')
  records=list(map(json.loads,raw.splitlines()))
 else:records=[json.loads(s) for s in (CACHE/'official-lineups-2023-final.jsonl').read_text(encoding='utf8').splitlines()]
 records=[r for r in records if str(r.get('fixture_id','')).endswith(str(args.season)) or r.get('season_id')==args.season]
 rows=[json.loads(s) for s in ROWS.read_text(encoding='utf8').splitlines()];selected={r['fixture_id']:r for r in rows if r['season']==args.season}
 if args.season==2026:
  groups=json.loads((ROWS.parent/'official-full-2026-cohorts.jsonl').read_text(encoding='utf8'))
  selected={k:v for k,v in selected.items() if k in set(groups['all'])}
  if len(selected)!=709:raise ValueError('2026_fixed_709_cohort_mismatch')
 records={r['fixture_id']:r for r in records if r['fixture_id'] in selected}
 path=CACHE/('actual-starters-'+str(args.season)+'.jsonl');prior=[json.loads(s) for s in path.read_text(encoding='utf8').splitlines()] if path.exists() else []
 done={x['fixture_id'] for x in prior}
 if len(prior)!=len(done):raise ValueError('duplicate_checkpoint')
 fetch=lineups.Fetcher(CACHE,args.max_requests,1.5);fetch.phase='actual-starters-'+str(args.season);stop='complete'
 archive=json.loads(Path(r'C:\Users\user\Documents\Codex\kbodata\data\raw\detail\kbo_baseball_2023_2026.json').read_text(encoding='utf8'))
 for fid,row in sorted(selected.items(),key=lambda item:(item[1].get('kickoff_at') or '',item[0])):
  if fid in done:continue
  source=records.get(fid);sm=bm=None
  try:
   if not source:raise ValueError('official_lineup_capture_missing')
   sm=source.get('schedule_evidence');schedule=json.loads(evidence(CACHE,sm));matches=[g for g in schedule['game'] if g.get('G_ID')==source['official_game_id']]
   if len(matches)!=1:raise ValueError('schedule_game_missing_or_duplicate')
   g=matches[0]
   for k,v in [('G_ID',source['official_game_id']),('G_DT',source['game_date']),('SEASON_ID',args.season),('LE_ID',1),('SR_ID',source['series_id']),('HEADER_NO',source['header_no']),('HOME_ID',source['home_team_id']),('AWAY_ID',source['away_team_id'])]:
    if str(g.get(k))!=str(v):raise ValueError('schedule_identity_mismatch_'+k)
   if (str(g.get('HOME_NM','')).strip(),str(g.get('AWAY_NM','')).strip())!=(row['home_id'],row['away_id']):raise ValueError('schedule_team_mismatch')
   params={'leId':source['league_id'],'srId':source['series_id'],'seasonId':source['season_id'],'gameId':source['official_game_id']}
   if args.cached_only:
    key=sha(json.dumps(['POST',BOX,params],sort_keys=True,ensure_ascii=False).encode())
    if not (CACHE/'requests'/(key+'.json')).exists():continue
   raw,bm=fetch.fetch('POST',BOX,params)
   def resolver(side,claim):
    box=archive.get(fid) or {};players=((box.get('data') or {}).get(side) or [])
    candidates={str(x['pcode']) for x in players if str(x.get('name') or '').strip()==claim and str(x.get('pcode') or '').isdigit()}
    if claim.isdigit():candidates={claim}
    if len(candidates)!=1:raise ValueError(side+'_actual_starter_identity_missing_or_ambiguous')
    pid=next(iter(candidates));pr,pm=fetch.fetch('GET',lineups.ROOT+'/Record/Player/PitcherDetail/Basic.aspx',{'playerId':pid})
    name=lineups.profile_name(pr)
    if not claim.isdigit() and name!=claim:raise ValueError(side+'_actual_starter_profile_name_mismatch')
    return pid,name,pm
   starters=parse_box(json.loads(raw),g,source,resolver)
   record={'fixture_id':fid,'season':args.season,'date':g['G_DT'],'official_game_id':g['G_ID'],'status':'ok','starters':starters,'schedule_evidence':sm,'actual_pitcher_record_evidence':bm,'historical_publication_time_proven':False,'historical_receipt_proven':False}
  except lineups.StopCapture as exc:
   if args.cached_only:continue
   stop=str(exc);break
  except (ValueError,KeyError,IndexError,TypeError,json.JSONDecodeError) as exc:
   record={'fixture_id':fid,'season':args.season,'status':'excluded','reason':str(exc)[:200],'schedule_evidence':sm,'actual_pitcher_record_evidence':bm}
  with path.open('a',encoding='utf8',newline='\n') as out:out.write(json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n')
  done.add(fid)
 info={'season':args.season,'population':len(selected),'processed':len(done),'remaining':len(selected)-len(done),'requests_started':fetch.requests,'stop_reason':stop,'finished_at':datetime.now(timezone.utc).isoformat()}
 (CACHE/('actual-starters-'+str(args.season)+'-progress.json')).write_text(json.dumps(info,indent=2),encoding='utf8');print(json.dumps(info))
if __name__=='__main__':main()
