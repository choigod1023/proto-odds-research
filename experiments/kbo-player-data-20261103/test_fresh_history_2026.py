"""Independent structural and scalar checks for the frozen fresh-history inputs."""
import gzip,hashlib,importlib.util,json,sys
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path
H=Path(__file__).resolve().parent;R=H.parents[1]
P=Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-model-20261103\experiments\kbo-player-model-20261103\adapter.py')
assert hashlib.sha256(P.read_bytes()).hexdigest()=='136a7c96624531010c6e247cc22fc6e5452680e8563a2f94e52a64e19c4a1ae0'
s=importlib.util.spec_from_file_location('frozen_adapter',P);adapter=importlib.util.module_from_spec(s);s.loader.exec_module(adapter)
sys.path.insert(0,str(H));import build_prior_projection as prior

def gz(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return list(map(json.loads,f))
games=gz(prior.GAMES);dates={x['source_fixture_id']:date.fromisoformat(x['date']) for x in games}; group=json.loads((H/'official-full-2026-cohorts.jsonl').read_text(encoding='utf8'));manifest=json.loads((H/'fresh-history-2026-manifest.json').read_text(encoding='utf8'))
assert len(dates)==len(games) and len(group['all'])==709
assert prior.innings_outs('5.2')==17 and prior.innings_outs('5.1')==16 and prior.innings_outs('5.3') is None
assert adapter.scalar('home_lineup_strength',[{'history':[{'ab':0,'hit':0,'bb':0}]}]) is None
assert adapter.scalar('home_lineup_strength',[{'history':[{'ab':0,'hit':0,'bb':0},{'ab':2,'hit':1,'bb':1}]}])==2/3
assert adapter.scalar('home_lineup_strength',[{'history':[{'ab':2,'hit':1,'bb':None}]}]) is None
assert adapter.scalar('home_starter_strength',{'appearances':[{'outs':0,'er':0}]}) is None
assert adapter.scalar('home_starter_strength',{'appearances':[{'outs':17,'er':2}]})==54/17
assert adapter.scalar('home_bullpen_load',{'games':[{'relief':{'outs':0}}]})==0.0
base={x['fixture_id']:x for x in gz(H/'prior-projected-features.jsonl.gz')}; results={};counts=Counter(); zero_sources=set()
for variant in ('projected','official_batting_only','official_starter_only','official_batting_and_starter'):
 p=H/('fresh-history-2026-'+variant+'.jsonl.gz');assert hashlib.sha256(p.read_bytes()).hexdigest()==manifest['outputs_sha256'][variant];rows=gz(p)
 assert [r['fixture_id'] for r in rows]==group['all'];results[variant]=rows
 for row in rows:
  fid=row['fixture_id'];day=dates[fid];assert row['completed_at'] is None and row['received_at'] is None and row['availability_tier']=='retrospective_unproven'
  adapter.check_nested(row['features'],fid,day,dates);adapter.check_nested(row['projection'],fid,day,dates)
  assert set(row['features'])==set(base[fid]['features'])
  for k,f in row['features'].items():
   assert f['completed_at'] is None and f['received_at'] is None
   adapter.scalar(k,f['value'])
   if not any(k.endswith('_'+z) for z in ('lineup_strength','starter_strength','bullpen_load')):assert f==base[fid]['features'][k]
   if k.endswith('_lineup_strength'):
    for player in f['value'] or []:
     hist=player['history'] or [];assert len(hist)<=10
     for x in hist:
      assert all(type(x[q]) is int and x[q]>=0 for q in ('ab','hit','bb')) and x['hit']<=x['ab']
      if x['ab']==x['bb']==0 and 'provenance' in x:zero_sources.add((x['fixture_id'],player['player_id']))
      if 'provenance' in x:assert x['provenance']['daily']['received_at'].startswith('2026-11-03')
     counts['player_histories']+=bool(hist)
   if k.endswith('_starter_strength') and f['value']:
    hist=f['value']['appearances'];assert len(hist)<=10
    assert len({x['fixture_id'] for x in hist})==len(hist)
    for x in hist:assert type(x['outs']) is int and x['outs']>=0 and type(x['er']) is int and x['er']>=0
   if k.endswith('_bullpen_load') and f['value']:
    for g in f['value']['games']:
     assert 1<=(day-date.fromisoformat(g['date'])).days<=2
     assert g['relief']['outs']==sum(x['outs'] for x in g['relief']['pitchers'])
  counts['variant_rows']+=1
for a,b,c,d in zip(*(results[k] for k in ('projected','official_batting_only','official_starter_only','official_batting_and_starter'))):
 for side in ('home','away'):
  assert a['features'][side+'_lineup_strength']==c['features'][side+'_lineup_strength']
  assert b['features'][side+'_lineup_strength']==d['features'][side+'_lineup_strength']
  assert a['features'][side+'_starter_strength']==b['features'][side+'_starter_strength']
  assert c['features'][side+'_starter_strength']==d['features'][side+'_starter_strength']
  assert all(x['features'][side+'_bullpen_load']==a['features'][side+'_bullpen_load'] for x in (b,c,d))
assert zero_sources
# Independently reconstruct every selected player's post-cutoff complete observation
# and ensure the retained rows are exactly the most recent ten across both sources.
K=Path(r'C:\Users\user\Documents\Codex\kbor\experiments\kbo-official-record-gap-20261103')
v1={x['fixture_id']:x for x in gz(K/'artifacts/normalized-records.jsonl.gz')};v2={x['fixture_id']:x for x in gz(K/'v2/artifacts/supplement-v2.jsonl.gz')}
new_bat=defaultdict(list);new_pit=defaultdict(list)
for fid in sorted(v1,key=lambda f:(dates[f],f)):
 r=v1[fid];supp=v2[fid]
 for side in ('home','away'):
  for x,y in zip(r['statistics']['batters'][side],supp['batters'][side]):
   if y['status']=='matched':new_bat[x['player_id']].append((dates[fid],fid,x['AB'],x['H'],y['BB']))
  pairs=list(zip(r['statistics']['pitchers'][side],supp['pitcher_id_supplement'][side]));ids=[x['player_id'] or (y['supplement_player_id'] if y['status']=='verified_official_profile' else None) for x,y in pairs];duplicates={pid for pid,n in Counter(ids).items() if pid and n>1}
  for (x,y),pid in zip(pairs,ids):
   if pid and pid not in duplicates and type(x['outs']) is int and type(x['ER']) is int:new_pit[pid].append((dates[fid],fid,x['outs'],x['ER']))
independent_checks=0
for variant in ('projected','official_batting_and_starter'):
 for row in results[variant]:
  day=dates[row['fixture_id']]
  for side in ('home','away'):
   for player in row['features'][side+'_lineup_strength']['value'] or []:
    hist=player['history'] or [];actual=[(dates[x['fixture_id']],x['fixture_id'],x['ab'],x['hit'],x['bb']) for x in hist if dates[x['fixture_id']]>date(2026,7,26)]
    expected=[x for x in new_bat[player['player_id']] if x[0]<day][-10:]
    assert actual==expected
    independent_checks+=1
   pitch=row['features'][side+'_starter_strength']['value']
   if pitch:
    actual=[(dates[x['fixture_id']],x['fixture_id'],x['outs'],x['er']) for x in pitch['appearances'] if dates[x['fixture_id']]>date(2026,7,26)]
    expected=[x for x in new_pit[pitch['projected_player_id']] if x[0]<day][-10:]
    assert actual==expected
    independent_checks+=1
p=H/'fresh-history-2026-incomplete-batters.jsonl.gz';missing=gz(p);assert hashlib.sha256(p.read_bytes()).hexdigest()==manifest['outputs_sha256']['incomplete_batters'];assert len(missing)==3026 and all(x['bb'] is None for x in missing)
print(json.dumps({'status':'ok','variant_rows':counts['variant_rows'],'player_histories':counts['player_histories'],'independent_new_history_checks':independent_checks,'distinct_zero_ab_and_bb_source_rows_retained':len(zero_sources),'incomplete_rows':len(missing),'scalar_edge_cases':'ok','nested_temporal_checks':'ok','factorial_and_fixed_core':'ok'}))
