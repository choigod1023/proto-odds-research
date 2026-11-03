"""Verify immutable source blobs before adapting worker-specific schemas."""
import hashlib,re,subprocess
from pathlib import Path

def committed_blob(repo,commit,path,expected_sha256):
 if not re.fullmatch(r'[0-9a-f]{40}',commit) or not re.fullmatch(r'[0-9a-f]{64}',expected_sha256):raise ValueError('full immutable commit and SHA-256 required')
 rel=Path(path)
 if rel.is_absolute() or '..' in rel.parts:raise ValueError('unsafe source path')
 raw=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+rel.as_posix()])
 if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('source Git blob SHA mismatch')
 return raw

def validate_official_identity(row,evidence,verified_hashes):
 """Canonical evidence must come from the pinned data-worker handoff."""
 if evidence.get('fixture_id')!=row['fixture_id'] or evidence.get('event_date')!=row['event_date'] or evidence.get('home_id')!=row['home_id'] or evidence.get('away_id')!=row['away_id']:raise ValueError('official fixture identity mismatch')
 if not evidence.get('official_game_id') or not re.fullmatch(r'[0-9a-f]{64}',evidence.get('source_sha256','')) or evidence['source_sha256'] not in verified_hashes:raise ValueError('official source evidence absent')
 if evidence.get('retrospective_only') is not True or evidence.get('historical_receipt_verified') is not False:raise ValueError('historical availability promotion')
 for side in ('home','away'):
  actual=evidence['sides'][side];roster=row['roster'][side]
  if actual.get('team_id')!=row[side+'_id'] or actual.get('hitters')!=roster['hitters'] or actual.get('starter')!=roster['starter']:raise ValueError('official roster identity mismatch')
  if actual.get('batting_basis') not in ('official_confirmed_starting_lineup','official_actual_starting_lineup') or actual.get('starter_basis') not in ('official_explicit_start_flag','official_verified_starting_field','official_verified_presentation_order'):raise ValueError('unverified actual starter or lineup')

def validate_projected_identity(row,projection,known_dates):
 """Projected lineup and starter selectors must cite only earlier fixtures."""
 from datetime import date
 import pipeline
 day=date.fromisoformat(row['event_date']);pipeline.original.adapter.check_nested(projection,row['fixture_id'],day,known_dates)
 for side in ('home','away'):
  line=projection[side+'_lineup'];starter=projection[side+'_starter'];roles=row['roster'][side]
  if line.get('basis')!='projected_from_prior_games' or starter.get('basis')!='projected_from_prior_games':raise ValueError('unregistered projected selector')
  if line.get('player_ids')!=roles['hitters'] or starter.get('player_id')!=roles['starter']:raise ValueError('projected roster and source selector differ')
  if roles['hitters'] is not None and not line.get('source_fixture_ids'):raise ValueError('projected lineup source missing')
  if roles['starter'] is not None:
   candidates=starter.get('rotation_candidates') or []
   if len(candidates)!=5 or len({x.get('player_id') for x in candidates})!=5 or any(not x.get('date') or not x.get('fixture_id') for x in candidates):raise ValueError('projected rotation selector incomplete')
   oldest=min(x['date'] for x in candidates);selected=[x['player_id'] for x in candidates if x['date']==oldest]
   if selected!=[roles['starter']]:raise ValueError('projected rotation selection mismatch')

def normalize_overlay(base,overlay,selector,evidence,known_dates,verified_hashes):
 """Convert a frozen source overlay to the exact scalar/roster model schema."""
 import pipeline
 if selector not in ('projected','actual') or overlay.get('fixture_id')!=base['fixture_id'] or set(overlay.get('features',{}))!=set(pipeline.FEATURES) or overlay.get('availability_tier')!='retrospective_unproven' or overlay.get('received_at') is not None or overlay.get('completed_at') is not None:raise ValueError('source overlay identity, schema or availability mismatch')
 from datetime import date
 for entry in overlay['features'].values():pipeline.original.adapter.check_nested(entry,base['fixture_id'],date.fromisoformat(base['event_date']),known_dates)
 side_roster={}
 for side in ('home','away'):
  if selector=='projected':
   side_roster[side]={'hitters':overlay['projection'][side+'_lineup']['player_ids'],'starter':overlay['projection'][side+'_starter']['player_id']}
  else:
   side_roster[side]={'hitters':evidence['sides'][side]['hitters'],'starter':evidence['sides'][side]['starter']}
 features={k:{'value':pipeline.original.adapter.scalar(k,v['value'])} for k,v in overlay['features'].items()};row={k:base[k] for k in ('fixture_id','season','event_date','home_id','away_id')};row.update({'features':features,'roster':side_roster});pipeline.validate_row(row,selector=='actual');lineage={k:overlay['features'][k] for k in pipeline.PLAYER};pipeline.validate_lineage(row['fixture_id'],row['event_date'],lineage,known_dates)
 if selector=='projected':validate_projected_identity(row,overlay['projection'],known_dates)
 else:validate_official_identity(row,evidence,verified_hashes)
 for side in ('home','away'):
  line=overlay['features'][side+'_lineup_strength']['value'];starter=overlay['features'][side+'_starter_strength']['value']
  if line is not None and [x['player_id'] for x in line]!=side_roster[side]['hitters']:raise ValueError('lineup player history not aligned')
  if starter is not None and starter['projected_player_id']!=side_roster[side]['starter']:raise ValueError('starter player history not aligned')
 return row,lineage
