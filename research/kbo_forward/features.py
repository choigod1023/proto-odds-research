"""Build frozen M0 features from received history; no fetching or persistence.

build_row(event, roster, history, received_at) accepts event identity/kickoff,
ordered string-ID roster, and history={seed, games, schedules}. Seed is a
point-in-time materialization received now (not evidence of a past receipt):
{through_date, received_at, games:[...]} or None. An incremental game is
{fixture_id,date,received_at,teams:{team:{runs_for,runs_against,relief}},
 batters:{player:{ab,hit,bb}},pitchers:{player:{er,outs}}}. Relief is the
frozen {outs,...} object or null. schedules[team] provides from_date,
through_date, received_at, events:[{fixture_id,date,status}], and source URI
and SHA of the received body. A claimed schedule is internally reconciled;
this module cannot authenticate the caller's source or guarantee completeness.
"""
import copy, gzip, hashlib, importlib.util, json, re
from urllib.parse import urlparse
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
V=ROOT/'experiments/kbo-m0-followup-20261103/vendor/evolution/common'
spec=importlib.util.spec_from_file_location('forward_feature_adapter',V/'vendor/original_adapter.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

def moment(s):
 d=datetime.fromisoformat(s)
 if d.tzinfo is None or d.utcoffset() is None:raise ValueError('explicit timezone required')
 return d.astimezone(timezone.utc)

def build_row(event,roster,history,received_at):
 now=moment(received_at);kickoff=moment(event['kickoff_at']);day=datetime.fromisoformat(event['kickoff_at']).astimezone(timezone(timedelta(hours=9))).date()
 if now>=kickoff or int(event['season'])!=day.year:raise ValueError('invalid capture date/time')
 official=event['official_game_id'];fid=official+str(event['season'])
 if not official or not event['home_id'] or not event['away_id'] or event['home_id']==event['away_id']:raise ValueError('invalid event identity')
 if set(roster)!={'home','away'}:raise ValueError('roster sides')
 for side in ('home','away'):
  r=roster[side];h=r['hitters'];st=r['starter']
  if not isinstance(st,str) or not st or not isinstance(h,list) or len(h)!=9 or len(set(h))!=9 or any(not isinstance(x,str) or not x for x in h):raise ValueError('ordered official roster IDs required')
 if set(roster['home']['hitters'])&set(roster['away']['hitters']) or roster['home']['starter']==roster['away']['starter']:raise ValueError('opposing IDs overlap')
 seed=history.get('seed');games=[];seen=set()
 if seed is not None:
  seed_through=date.fromisoformat(seed['through_date'])
  if seed_through>=day or moment(seed['received_at'])>now:raise ValueError('seed unavailable or after target')
  seed_receipt=seed['received_at']
  for game in seed['games']:
   if date.fromisoformat(game['date'])>seed_through:raise ValueError('seed contains later game')
   x=copy.deepcopy(game);x['received_at']=seed_receipt;games.append(x)
 else:seed_through=None
 seed_index={g['fixture_id']:g for g in games};increment_ids=set()
 for item in history.get('games',[]):
  g=copy.deepcopy(item);gid=g['fixture_id']
  if gid in increment_ids:raise ValueError('duplicate incremental game')
  increment_ids.add(gid)
  if gid not in seed_index:
   gd=date.fromisoformat(g['date'])
   if seed_through is not None and gd<=seed_through and all(gd<=date.fromisoformat(seed.get('team_through',{}).get(team,seed['through_date'])) for team in g.get('teams',{})):raise ValueError('increment precedes known team coverage')
   games.append(g);continue
  old=seed_index[gid]
  if old['date']!=g['date']:raise ValueError('seed and incremental date conflict')
  if moment(g['received_at'])>now:raise ValueError('future incremental receipt')
  for section in ('teams','batters','pitchers'):
   dest=old.setdefault(section,{})
   for owner,values in g.get(section,{}).items():
    slot=dest.setdefault(owner,{})
    for key,value in values.items():
     if key in slot and slot[key] is not None and value is not None and slot[key]!=value:raise ValueError('seed and incremental observation conflict')
     if value is not None:slot[key]=copy.deepcopy(value)
  old['received_at']=max((old['received_at'],g['received_at']),key=moment)
 for g in games:
  gd=date.fromisoformat(g['date']);gid=g['fixture_id']
  if not isinstance(gid,str) or not gid or gid in seen or gid in (fid,official) or gd>=day or moment(g['received_at'])>now:raise ValueError('duplicate, target, future, or unavailable history')
  seen.add(gid)
  for mapping in (g.get('teams',{}),g.get('batters',{}),g.get('pitchers',{})):
   if any(not isinstance(k,str) or not k for k in mapping):raise ValueError('bad history owner ID')
 games.sort(key=lambda g:(g['date'],g['fixture_id']))
 def coverage(team):
  s=history.get('schedules',{}).get(team)
  if s is None or seed_through is None:return False
  # Each team's last roster snapshot can precede the shared seed cutoff.
  own_through=date.fromisoformat(seed.get('team_through',{}).get(team,seed['through_date']))
  if moment(s['received_at'])>now:return False
  start=date.fromisoformat(s['from_date']);end=date.fromisoformat(s['through_date'])
  if start>own_through+timedelta(days=1) or end<day-timedelta(days=1):return False
  uri=urlparse(str(s.get('uri','')));body=s.get('body')
  if uri.scheme!='https' or uri.hostname not in ('koreabaseball.com','www.koreabaseball.com') or not isinstance(body,str):return False
  if not re.fullmatch('[0-9a-f]{64}',str(s.get('sha256',''))) or hashlib.sha256(body.encode('utf8')).hexdigest()!=s['sha256']:return False
  events=s['events'];listed={}
  for e in events:
   gd=date.fromisoformat(e['date']);eid=e['fixture_id'];status=e['status']
   if eid in listed or not start<=gd<=end or gd>=day or status not in ('final','cancelled','postponed'):return False
   listed[eid]=e
  actual={g['fixture_id']:g for g in games if team in g.get('teams',{}) and start<=date.fromisoformat(g['date'])<=end}
  finals={eid:e for eid,e in listed.items() if e['status']=='final'}
  if set(finals)!=set(actual):return False
  if any(finals[k]['date']!=actual[k]['date'] for k in finals):return False
  return True
 def entry(value):return {'value':value}
 raw={};features={}
 def put(name,value):
  def source_ids(x):
   if isinstance(x,dict):
    out=[]
    for k,v in x.items():
     if k in ('fixture_id','id') and isinstance(v,str):out.append(v)
     else:out.extend(source_ids(v))
    return out
   if isinstance(x,list):return [z for v in x for z in source_ids(v)]
   return []
  ids=sorted(set(source_ids(value)))
  raw[name]={'value':value,'source_fixture_ids':ids};features[name]=entry(adapter.scalar(name,value))
 for side in ('home','away'):
  team=event[side+'_id'];teamgames=[g for g in games if team in g.get('teams',{})];covered=coverage(team)
  recent=[]
  for g in teamgames[-10:]:
   v=g['teams'][team]
   recent.append({'fixture_id':g['fixture_id'],'date':g['date'],'runs_for':v.get('runs_for'),'runs_against':v.get('runs_against')})
  put(side+'_team_strength',recent if covered and recent and all(adapter.nonnegative(x['runs_for'],x['runs_against']) for x in recent) else None)
  rest=(day-date.fromisoformat(teamgames[-1]['date'])).days-1 if covered and teamgames else None
  put(side+'_rest_days',rest)
  players=[]
  for pid in roster[side]['hitters']:
   xs=[{'fixture_id':g['fixture_id'],'date':g['date'],**{k:v for k,v in g['batters'][pid].items() if k in ('ab','hit','bb','hr')}} for g in games if pid in g.get('batters',{})][-10:]
   players.append({'player_id':pid,'history':xs})
  put(side+'_lineup_strength',players if any(p['history'] for p in players) else None)
  pid=roster[side]['starter'];xs=[{'fixture_id':g['fixture_id'],'date':g['date'],**{k:v for k,v in g['pitchers'][pid].items() if k in ('er','outs','r','hit','bb','hr','kk')}} for g in games if pid in g.get('pitchers',{})][-10:]
  put(side+'_starter_strength',{'projected_player_id':pid,'appearances':xs} if xs else None)
  recent_relief=[g for g in teamgames if 1<=(day-date.fromisoformat(g['date'])).days<=2]
  if covered and recent_relief and all(isinstance(g['teams'][team].get('relief'),dict) and adapter.nonnegative(g['teams'][team]['relief'].get('outs')) for g in recent_relief):
   relief={'games':[{'id':g['fixture_id'],'date':g['date'],'relief':copy.deepcopy(g['teams'][team]['relief'])} for g in recent_relief],'kind':'previous_1_2_calendar_days_relief_appearances_and_outs_proxy','schedule_coverage_verified':True}
  else:relief=None
  put(side+'_bullpen_load',relief)
 put('park_factor',None)
 adapter.check_nested(raw,fid,day,{g['fixture_id']:date.fromisoformat(g['date']) for g in games})
 return {'fixture_id':fid,'event_date':day.isoformat(),'season':event['season'],'home_id':event['home_id'],'away_id':event['away_id'],'roster':copy.deepcopy(roster),'raw_features':raw,'features':features}

def seed_from_frozen(received_at, target_date):
 """Materialize cached historical feature observations; current receipt only.

 Returns the `history['seed']` shape. No network access. This is not evidence
 that any historical source was available before its original game.
 """
 moment(received_at);target=date.fromisoformat(target_date);rows=[]
 for name in ('train-actual.jsonl.gz','test-actual.jsonl.gz'):
  with gzip.open(V/'inputs'/name,'rt',encoding='utf8') as f:
   for line in f:
    row=json.loads(line)
    if date.fromisoformat(row['event_date'])<=target:rows.append(row)
 if not rows:raise ValueError('no frozen seed data before target')
 through=max(date.fromisoformat(r['event_date']) for r in rows)-timedelta(days=1)
 team_through={}
 for r in rows:
  for side in ('home','away'):
   team=r[side+'_id'];day=date.fromisoformat(r['event_date'])-timedelta(days=1)
   team_through[team]=max(day,team_through.get(team,day))
 if through>=target:raise ValueError('seed not prior to target')
 games={}
 def game(fid,ds):
  day=date.fromisoformat(ds)
  if day>through:raise ValueError('seed history after seed cutoff')
  g=games.setdefault(fid,{'fixture_id':fid,'date':ds,'teams':{},'batters':{},'pitchers':{}})
  if g['date']!=ds:raise ValueError('seed fixture date conflict')
  return g
 def merge(dst,owner,val):
  d=dst.setdefault(owner,{})
  for k,v in val.items():
   if k in d and d[k]!=v and d[k] is not None and v is not None:raise ValueError('conflicting frozen observation')
   if v is not None:d[k]=v
 for row in rows:
  for side in ('home','away'):
   team=row[side+'_id'];features=row['raw_features']
   for x in features[side+'_team_strength']['value'] or []:
    g=game(x['fixture_id'],x['date']);merge(g['teams'],team,{k:x.get(k) for k in ('runs_for','runs_against')})
   for player in features[side+'_lineup_strength']['value'] or []:
    for x in player.get('history') or []:
     g=game(x['fixture_id'],x['date']);merge(g['batters'],player['player_id'],{k:x.get(k) for k in ('ab','hit','bb','hr')})
   pitch=features[side+'_starter_strength']['value']
   if pitch:
    for x in pitch.get('appearances') or []:
     g=game(x['fixture_id'],x['date']);merge(g['pitchers'],pitch['projected_player_id'],{k:x.get(k) for k in ('er','outs','r','hit','bb','hr','kk')})
   bullpen=features[side+'_bullpen_load']['value']
   if bullpen:
    for x in bullpen.get('games') or []:
     g=game(x['id'],x['date']);merge(g['teams'],team,{'relief':x.get('relief')})
 return {'through_date':through.isoformat(),'team_through':{k:v.isoformat() for k,v in team_through.items()},'received_at':received_at,'games':sorted(games.values(),key=lambda g:(g['date'],g['fixture_id'])),'basis':'frozen retrospective raw features, materialized at current receipt; not historical receipt proof'}
