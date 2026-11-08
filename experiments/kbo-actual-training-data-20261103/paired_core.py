"""Paired roster feature transformations; no fit, prediction, labels or I/O."""
import copy

PLAYER_KEYS=('lineup_strength','starter_strength')
def valid_batter(x):
 return all(type(x.get(k)) is int and x[k]>=0 for k in ('ab','hit','bb')) and x['hit']<=x['ab']
def valid_pitcher(x):return all(type(x.get(k)) is int and x[k]>=0 for k in ('outs','er'))
def previous(history,target_day,valid):
 return [x for x in history if x['date']<target_day and valid(x)][-10:]
def value(data,source):
 return {'value':data,'source_fixture_ids':sorted({x['fixture_id'] for x in source}),
         'latest_source_date':max((x['date'] for x in source),default=None),
         'completed_at':None,'received_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received'}
def build_actual(base,roster,batter_history,pitcher_history,day):
 """Leave every non-player feature unchanged, replacing only selected histories."""
 if roster.get('status')!='complete':raise ValueError('actual_roster_not_complete')
 all_hitters=[x['player_id'] for s in ('home','away') for x in roster['hitters'][s]]
 if len(all_hitters)!=18 or len(set(all_hitters))!=18:raise ValueError('actual_cross_team_batter_id_duplicate')
 if roster['starters']['home']['player_id']==roster['starters']['away']['player_id']:raise ValueError('actual_cross_team_starter_id_duplicate')
 out=copy.deepcopy(base)
 for side in ('home','away'):
  players=roster['hitters'][side];starter=roster['starters'][side]
  if len(players)!=9 or [x['slot'] for x in players]!=list(range(1,10)) or len({x['player_id'] for x in players})!=9:raise ValueError('invalid_actual_batting_roster')
  if not starter['actual_record_verified'] or not starter['player_id']:raise ValueError('starter_not_verified')
  profiles=[];all_history=[]
  for player in players:
   hist=previous(batter_history.get(player['player_id'],[]),day,valid_batter)
   profiles.append({'player_id':player['player_id'],'history':copy.deepcopy(hist) or None});all_history.extend(hist)
  out['features'][side+'_lineup_strength']=value(profiles,all_history)
  hist=previous(pitcher_history.get(starter['player_id'],[]),day,valid_pitcher)
  out['features'][side+'_starter_strength']=value({'projected_player_id':starter['player_id'],'appearances':copy.deepcopy(hist)} if hist else None,hist)
 return out

def roles_projected(src):
 return {s:{'hitters':src['projection'][s+'_lineup']['player_ids'],'starter':src['projection'][s+'_starter']['player_id']} for s in ('home','away')}
def roles_actual(roster):
 return {s:{'hitters':[x['player_id'] for x in roster['hitters'][s]],'starter':roster['starters'][s]['player_id']} for s in ('home','away')}
def verify_shared_histories(projected,actual):
 """Shared identity must expose byte-equivalent canonical player history."""
 import json
 canonical=lambda obj:json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
 for side in ('home','away'):
  before=projected['features'][side+'_lineup_strength']['value'] or []
  after=actual['features'][side+'_lineup_strength']['value'] or []
  b={x['player_id']:x['history'] for x in before};a={x['player_id']:x['history'] for x in after}
  if len(b)!=len(before) or len(a)!=len(after):raise ValueError('duplicate_lineup_player_id')
  for pid in set(a)&set(b):
   if canonical(a[pid])!=canonical(b[pid]):raise ValueError('shared_batter_history_mismatch')
  p=projected['features'][side+'_starter_strength']['value'];q=actual['features'][side+'_starter_strength']['value']
  if p and q and p['projected_player_id']==q['projected_player_id'] and canonical(p['appearances'])!=canonical(q['appearances']):raise ValueError('shared_starter_history_mismatch')
def verify_projected_history(source,batter_history,pitcher_history,day):
 for side in ('home','away'):
  selected=source['projection'][side+'_lineup']['player_ids'];profiles=source['features'][side+'_lineup_strength']['value']
  if selected is None:
   if profiles is not None:raise ValueError('projected_lineup_mismatch')
  else:
   if profiles is None or [p['player_id'] for p in profiles]!=selected:raise ValueError('projected_lineup_mismatch')
   for p in profiles:
    if (p['history'] or [])!=previous(batter_history.get(p['player_id'],[]),day,valid_batter):raise ValueError('projected_batter_history_mismatch')
  pid=source['projection'][side+'_starter']['player_id'];profile=source['features'][side+'_starter_strength']['value']
  expected=previous(pitcher_history.get(pid,[]),day,valid_pitcher) if pid else []
  if expected:
   if not profile or profile['projected_player_id']!=pid or profile['appearances']!=expected:raise ValueError('projected_pitcher_history_mismatch')
  elif profile is not None:raise ValueError('projected_pitcher_history_mismatch')
def model_row(source,raw,roles,adapter):
 return {'fixture_id':source['fixture_id'],'event_date':raw['event_date'],'season':raw['season'],'home_id':raw['home_id'],'away_id':raw['away_id'],'availability_tier':'retrospective_unproven','historical_receipt_proven':False,
         'features':{key:{'value':adapter.scalar(key,entry['value'])} for key,entry in source['features'].items()},'roster':roles,'raw_features':source['features']}
def training_pair(source,raw,roster,batter_history,pitcher_history,adapter):
 if source['fixture_id']!=roster['fixture_id'] or source['fixture_id']!=raw['fixture_id']:raise ValueError('paired_fixture_mismatch')
 if (roster['teams']['home'],roster['teams']['away'])!=(raw['home_id'],raw['away_id']):raise ValueError('paired_team_mismatch')
 if roster['date']!=raw['event_date'] or roster['season']!=raw['season']:raise ValueError('paired_date_mismatch')
 verify_projected_history(source,batter_history,pitcher_history,raw['event_date'])
 actual=build_actual(source,roster,batter_history,pitcher_history,raw['event_date'])
 verify_shared_histories(source,actual)
 for key,v in source['features'].items():
  if not key.endswith(PLAYER_KEYS) and actual['features'][key]!=v:raise ValueError('paired_nonplayer_changed')
 return model_row(source,raw,roles_projected(source),adapter),model_row(actual,raw,roles_actual(roster),adapter)
