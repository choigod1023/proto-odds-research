"""Adapt committed 58556de paired inputs with independent identity/lineage checks."""
import collections,gzip,hashlib,json
from datetime import date
from pathlib import Path
import pipeline as p
import workflow
P=Path(__file__).resolve().parent;I=P/'inputs'
EXPECTED_COMMIT='58556de08c64604276de8682373d3ccc8d535d7c';MAN_SHA='8be3db58b4915383acd1f375a1e244840c22fcf6b2a2a40cf71a9bf5441e84f9';TEAM={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def rows(name):return [json.loads(x) for x in gzip.decompress((I/name).read_bytes()).splitlines()]
def verify_snapshot():
 p.verify_pinned_code();idx=json.loads((I/'snapshot-index.json').read_bytes());raw=(I/'paired-inputs-manifest.json').read_bytes()
 if idx['paired_source_commit']!=EXPECTED_COMMIT or idx['paired_manifest_sha256']!=MAN_SHA or sha(raw)!=MAN_SHA:raise ValueError('source manifest pin mismatch')
 man=json.loads(raw)
 if man['parent_contract_sha256']!=p.CONTRACT_SHA or man['status']!='finalized_paired_research_inputs' or man['point_in_time_proven']!=0 or man['no_labels_read'] is not True or man['no_model_fit_or_score'] is not True:raise ValueError('invalid source manifest')
 for name,digest in idx['files'].items():
  if sha((I/name).read_bytes())!=digest:raise ValueError('snapshot asset hash mismatch: '+name)
 for key,digest in man['output_sha256'].items():
  if idx['files'].get('paired-'+key+'.jsonl.gz')!=digest:raise ValueError('paired input SHA differs from source manifest')
 fixed={'features.jsonl.gz':'346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42','development-labels.jsonl.gz':'22f3691f1e9017bbcfd9b2bde35ca0f6c208e292f117d056dd5271a80e9f6a33','prior-projected-features.jsonl.gz':'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae','fresh-history-2026-projected.jsonl.gz':'8b090ed9b204a50584c5e2a854bd0c6210a133a8518de78db51ca8f4217aa23f','fresh-history-2026-official_batting_and_starter.jsonl.gz':'82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706'}
 if any(idx['files'].get(k)!=v for k,v in fixed.items()):raise ValueError('previous data SHA pin mismatch')
 for year in ('2023','2024','2025'):
  name='actual-rosters-'+year+'.jsonl.gz';expected=man['source_rosters'][year]['sha256'];raw=(I/name).read_bytes();raw=gzip.decompress(raw) if year=='2024' else raw
  if sha(raw)!=expected:raise ValueError('source roster Git blob hash mismatch: '+year)
 if idx['files']['actual-starters-2026.jsonl.gz']!=man['verification']['sha256']:raise ValueError('2026 starter blob mismatch')
 return man,idx

def raw_roster(rec,year):
 if rec['season']!=year:raise ValueError('source roster year mismatch')
 if year==2023:
  if rec['status']!='complete' or rec['exclusions'] or rec['source_kind']!='official_retrospective_identity_only' or rec['historical_receipt_proven'] is not False or rec['historical_publication_time_proven'] is not False or rec['target_player_stats_used_as_features'] is not False:raise ValueError('invalid 2023 source roster')
  sides={s:(rec['hitters'][s],rec['starters'][s],rec['teams'][s]) for s in ('home','away')}
 elif year==2024:
  if rec['exclusion_reasons'] or rec['identity_tier']!='retrospective_only' or rec['eligible_as_pregame_feature'] is not False or rec['historical_receipt_time'] is not None or rec['historical_publication_time'] is not None or not rec['lineup_semantics'].startswith('confirmed_official_lineup_'):raise ValueError('invalid 2024 source roster')
  sides={s:(rec[s]['ordered_hitters'],rec[s]['starter'],rec[s+'_team']) for s in ('home','away')}
 else:
  if rec['complete_verified'] is not True or rec['exclusion_reasons'] or rec['lineup_confirmed'] is not True or rec['eligible_as_pregame_feature'] is not False or rec['historical_receipt_proven'] is not False or rec['historical_publication_time_proven'] is not False or not rec['batting_roster_semantics'].startswith('official_confirmed_lineup_'):raise ValueError('invalid 2025 source roster')
  sides={s:(rec[s]['ordered_hitters'],rec[s]['starter'],rec[s]['team']['name']) for s in ('home','away')}
 if not rec.get('official_game_id'):raise ValueError('official game id missing')
 for side,(hitters,starter,team) in sides.items():
  if len(hitters)!=9 or [z['slot'] for z in hitters]!=list(range(1,10)) or len({z['player_id'] for z in hitters})!=9 or not starter or starter.get('actual_record_verified') is not True or not starter.get('player_id'):raise ValueError('invalid official lineup or starter')
  basis=(starter.get('evidence') or {}).get('basis') if year==2023 else starter.get('evidence') if year==2024 else starter.get('identity_basis')
  allowed={2023:{'official_completed_boxscore_unique_first_starting_flag_and_official_schedule_same_team_name_or_id','official_completed_boxscore_unique_first_starting_flag_and_official_pitcher_profile'},2024:{'official_schedule_starting_pitcher_id_and_name_exact_match_official_boxscore_start_flag','official_schedule_id_and_same_game_archived_id_and_official_current_profile; explicit_boxscore_start_flag'},2025:{'official_numeric_starter_marker_and_matching_schedule_identity','unique_official_named_starter_same_schedule_id_and_official_profile','unique_same_day_official_pitcher_daily_roles_plus_schedule_and_box'}}
  if basis not in allowed[year]:raise ValueError('unverified source starter basis')
 return sides

def evidence(row,rosters,starter2026,official2026,man):
 fid=row['fixture_id'];year=row['season']
 if year<2026:
  record=rosters[year][fid];sides=raw_roster(record,year);day=record['date'];gid=record['official_game_id'];blobsha=man['source_rosters'][str(year)]['sha256'];out={}
  for side,(hitters,starter,team) in sides.items():
   if team!=row[side+'_id']:raise ValueError('source roster team mismatch')
   out[side]={'team_id':team,'hitters':[x['player_id'] for x in hitters],'starter':starter['player_id'],'batting_basis':'official_confirmed_starting_lineup','starter_basis':'official_explicit_start_flag'}
 else:
  record=starter2026[fid];info=official2026[fid]['official_lineup_context'];day=record['date'];gid=record['official_game_id'];blobsha=man['verification']['sha256'];out={}
  if record['status']!='complete' or record['exclusions'] or record['historical_receipt_proven'] is not False or gid!=info['official_game_id'] or any(info[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('invalid 2026 actual source')
  for side in ('home','away'):
   starter=record['starters'][side];line=info['lineups'][side]
   if record['teams'][side]!=TEAM.get(row[side+'_id']) or info[side+'_team_id']!=record['teams'][side] or not starter['actual_record_verified'] or starter['evidence']['basis']!='official_completed_boxscore_unique_first_starting_flag_and_official_schedule_same_team_name_or_id' or [x['order'] for x in line]!=list(range(1,10)):raise ValueError('2026 source identity mismatch')
   out[side]={'team_id':row[side+'_id'],'hitters':[x['player_id'] for x in line],'starter':starter['player_id'],'batting_basis':'official_confirmed_starting_lineup','starter_basis':'official_explicit_start_flag'}
 if day.replace('-','')!=row['event_date'].replace('-',''):raise ValueError('official game date mismatch')
 if not gid or gid[:8]!=row['event_date'].replace('-',''):raise ValueError('official game date key mismatch: '+fid)
 exact=fid==gid+str(year);synthetic_year=len(fid)==len(gid)+4 and fid[:4] in tuple(str(i)*4 for i in range(1,10)) and fid[4:-4]==gid[4:] and fid[-4:]==str(year);score_key=fid==f"score:{row['event_date']}:{row['home_id']}:{row['away_id']}"
 if not (exact or synthetic_year or score_key):raise ValueError('official game key mismatch: '+fid+' vs '+str(gid))
 return {'fixture_id':fid,'event_date':row['event_date'],'home_id':row['home_id'],'away_id':row['away_id'],'official_game_id':gid,'source_sha256':blobsha,'retrospective_only':True,'historical_receipt_verified':False,'sides':out}

def check_shared(left,right):
 for key in p.FEATURES:
  if key not in p.ROSTER_FEATURES and left['raw_features'][key]!=right['raw_features'][key]:raise ValueError('matched nonroster raw input changed')
 for left_side in ('home','away'):
  for right_side in ('home','away'):
   l=left['raw_features'][left_side+'_lineup_strength']['value'] or [];r=right['raw_features'][right_side+'_lineup_strength']['value'] or [];aa={x['player_id']:x['history'] for x in l};bb={x['player_id']:x['history'] for x in r}
   if any(aa[x]!=bb[x] for x in set(aa)&set(bb)):raise ValueError('shared hitter history mismatch')
   l=left['raw_features'][left_side+'_starter_strength']['value'];r=right['raw_features'][right_side+'_starter_strength']['value']
   if left['roster'][left_side]['starter'] is not None and left['roster'][left_side]['starter']==right['roster'][right_side]['starter'] and l!=r:raise ValueError('shared starter history mismatch')

def load():
 man,idx=verify_snapshot();base=p.index(rows('features.jsonl.gz'));known={fid:date.fromisoformat(x['event_date']) for fid,x in base.items()};proofs={2023:p.index(rows('prior-projected-features.jsonl.gz')),2026:p.index(rows('fresh-history-2026-projected.jsonl.gz'))};official=p.index(rows('fresh-history-2026-official_batting_and_starter.jsonl.gz'));rosters={y:p.index(rows('actual-rosters-'+str(y)+'.jsonl.gz')) for y in (2023,2024,2025)};starters=p.index(rows('actual-starters-2026.jsonl.gz'));hashes={man['source_rosters'][str(y)]['sha256'] for y in (2023,2024,2025)}|{man['verification']['sha256']};out={}
 for group,seasons in [('train',{2023,2024,2025}),('test',{2026})]:
  original={s:p.index(rows('paired-'+group+'-'+s+'.jsonl.gz')) for s in p.SELECTORS}
  if set(original['projected'])!=set(original['actual']):raise ValueError('paired source fixture mismatch')
  norm={s:[] for s in p.SELECTORS};lineage={s:{} for s in p.SELECTORS};evidence_map={};projection_map={}
  for fid,left in original['projected'].items():
   right=original['actual'][fid];check_shared(left,right)
   for sel,record in (('projected',left),('actual',right)):
    expected={'availability_tier','away_id','event_date','features','fixture_id','historical_receipt_proven','home_id','raw_features','roster','season'}
    if set(record)!=expected or record['availability_tier']!='retrospective_unproven' or record['historical_receipt_proven'] is not False or fid not in base or any(record[k]!=base[fid][k] for k in ('season','event_date','home_id','away_id')):raise ValueError('paired record metadata mismatch')
    for k,v in record['raw_features'].items():
     p.original.adapter.check_nested(v,fid,known[fid],known)
     if p.original.adapter.scalar(k,v['value'])!=record['features'][k]['value']:raise ValueError('raw scalar mismatch')
    row={k:record[k] for k in ('fixture_id','season','event_date','home_id','away_id','features','roster')};norm[sel].append(row);lineage[sel][fid]={k:record['raw_features'][k] for k in p.PLAYER}
   evidence_map[fid]=evidence(norm['actual'][-1],rosters,starters,official,man);projection_map[fid]=proofs[2026 if left['season']==2026 else 2023][fid]['projection']
  out[group]=workflow.validate_snapshot(norm,lineage,evidence_map,projection_map,known,hashes,seasons)
 counts=collections.Counter(x['season'] for x in out['train']['actual']);test=len(out['test']['actual'])
 if dict(counts)!={2023:723,2024:728,2025:731} or test!=709:raise ValueError('manifest population mismatch')
 dev_all=p.index(rows('development-labels.jsonl.gz'));ids={x['fixture_id'] for x in out['train']['actual']};labels={fid:dev_all[fid] for fid in ids}
 if len(labels)!=2182 or any(x.get('assumed_completed_date','')[:4]=='2026' for x in labels.values()):raise ValueError('invalid development label set')
 return out['train'],labels,out['test'],man
