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
