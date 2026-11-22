"""Rebuild 2023 and 2026 source input pairs and independently check roster and time boundaries."""
import collections,copy,gzip,json,subprocess,sys
from pathlib import Path
from datetime import date
import reference as ref
ROOT=Path(r'C:\Users\user\Documents\Codex\kbtdata');BASE=Path(r'C:\Users\user\Documents\Codex\kbodata');PREFIX='experiments/kbo-actual-training-data-20261103';C='e6068b83e1b076f0aa24a71a33a77930d4c09f2c';B='13291d23e855e0d500327cb39ac64a480e395837'
def pin(repo,commit,rel):
 p=repo/rel;raw=p.read_bytes();expected=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+rel]);
 if raw.replace(b'\r\n',b'\n')!=expected.replace(b'\r\n',b'\n'):raise ValueError('live code differs from pinned Git: '+rel)
def assert_prior_tree(value,fid,day):
 if isinstance(value,dict):
  if 'fixture_id' in value and 'date' in value:
   if value['fixture_id']==fid or str(value['date'])>=day:raise ValueError('same-day, future or target fixture '+fid)
  for v in value.values():assert_prior_tree(v,fid,day)
 elif isinstance(value,list):
  for v in value:assert_prior_tree(v,fid,day)
def main():
 for name in ('assemble_paired.py','paired_core.py','history_index.py','build_rosters_2023.py','collect_lineups.py','collect_actual_starters.py'):
  pin(ROOT,C,PREFIX+'/'+name)
 pin(BASE,B,'experiments/kbo-player-data-20261103/build_prior_projection.py')
 sys.path.insert(0,str(ROOT/PREFIX));import assemble_paired as a
 from paired_core import training_pair,roles_projected,model_row,previous,valid_pitcher,value
 adapter=a.adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=a.old_inputs();dates={fid:date.fromisoformat(g['date']) for fid,g in gmap.items()};counts=collections.Counter()
 def check(p,q,fid,day):
  if p['fixture_id']!=q['fixture_id'] or p['fixture_id']!=fid or p['event_date']!=q['event_date']:raise ValueError('pair mismatch '+fid)
  for x in (p,q):
   ref.assert_roster_feature_identity(x,x['raw_features']);assert_prior_tree(x['raw_features'],fid,day)
   for k,v in x['raw_features'].items():
    if k.endswith(('lineup_strength','starter_strength','bullpen_load')):
     if v.get('availability_basis')!='prior_calendar_day_assumed_completed_not_received' or v.get('received_at') is not None or v.get('completed_at') is not None:raise ValueError('unsupported historical availability '+fid+' '+k)
     for source_id in v.get('source_fixture_ids',[]):
      if source_id not in gmap or source_id==fid or gmap[source_id]['date']>=day:raise ValueError('invalid source fixture '+fid+' '+k)
     computed=ref.scalars(k,v['value']);observed=x['features'][k]['value']
     if computed!=observed and not(computed is not None and observed is not None and abs(computed-observed)<1e-12):raise ValueError('scalar mismatch '+fid+' '+k)
  for k in p['features']:
   if not k.endswith(('lineup_strength','starter_strength')) and p['features'][k]!=q['features'][k]:raise ValueError('non-roster feature changed '+fid+' '+k)
  counts['paired_'+day[:4]]+=1
 source=a.index(a.gz(a.pin(ROOT,C,PREFIX+'/actual-rosters-2023.jsonl.gz','53ea290c9bfe40dd792ddfd6b952faae4ee2d151176e0729a00abd8d216c9ff4')))
 for fid,r in source.items():
  x=rows[fid];day=gmap[fid]['date'];base={'fixture_id':fid,'event_date':day,'season':2023,'home_id':x['home_id'],'away_id':x['away_id']};p,q=training_pair(baseline[fid],base,r,bh,ph,adapter);check(p,q,fid,day)
  for side in ('home','away'):
   for h in q['raw_features'][side+'_lineup_strength']['value']:
    history=h['history'] or [];expected=[v for v in bh.get(h['player_id'],[]) if v['date']<day and all(type(v.get(k)) is int and v[k]>=0 for k in ('ab','hit','bb')) and v['hit']<=v['ab']][-10:]
    if history!=expected:raise ValueError('batter last-10 mismatch '+fid)
    counts['2023_missing_batter_history']+=int(not history)
   pval=q['raw_features'][side+'_starter_strength']['value'];counts['2023_missing_starter_history']+=int(pval is None)
   if pval:
    expected=[v for v in ph.get(pval['projected_player_id'],[]) if v['date']<day and all(type(v.get(k)) is int and v[k]>=0 for k in ('er','outs'))][-10:]
    if pval['appearances']!=expected:raise ValueError('pitcher last-10 mismatch '+fid)
 groups=json.loads(a.fixed_file(prior.OUT/'official-full-2026-cohorts.jsonl'));ids=groups['all'];verify=a.index(a.gz(a.pin(ROOT,C,PREFIX+'/actual-starters-2026.jsonl.gz','ca1d6dbc1f301ee4681237cbf1cd864df5079a3ab9364c2f7b50129a9574a69d')))
 old={n:a.index(a.gz(a.fixed_file(prior.OUT/('fresh-history-2026-'+n+'.jsonl.gz')))) for n in ('projected','official_batting_and_starter')};fresh=a.fresh_pitch(prior,games,pit)
 if set(ids)!=set(verify) or any(set(ids)!=set(z) for z in old.values()):raise ValueError('2026 population mismatch')
 for fid in ids:
  v=verify[fid];projected=old['projected'][fid];actual=copy.deepcopy(old['official_batting_and_starter'][fid]);day=gmap[fid]['date'];x=rows[fid];roles={}
  if v['status']!='complete' or v['official_game_id']!=actual['official_lineup_context']['official_game_id']:raise ValueError('verification mismatch '+fid)
  for side in ('home','away'):
   hit=actual['official_lineup_context']['lineups'][side];ids0=[i['player_id'] for i in hit];pid=v['starters'][side]['player_id'];hist=previous(fresh.get(pid,[]),day,valid_pitcher)
   if [i['order'] for i in hit]!=list(range(1,10)) or len(set(ids0))!=9 or v['teams'][side]!=actual['official_lineup_context'][side+'_team_id']:raise ValueError('official roster mismatch '+fid)
   actual['features'][side+'_starter_strength']=value({'projected_player_id':pid,'appearances':copy.deepcopy(hist)} if hist else None,hist);roles[side]={'hitters':ids0,'starter':pid};counts['2026_missing_starter_history']+=int(not hist)
  row={'fixture_id':fid,'event_date':day,'season':2026,'home_id':x['home_id'],'away_id':x['away_id']}
  p=model_row(projected,row,roles_projected(projected),adapter);q=model_row(actual,row,roles,adapter);check(p,q,fid,day)
  for side in ('home','away'):
   h=q['raw_features'][side+'_starter_strength']['value']
   if h and len(h['appearances'])>10:raise ValueError('too many pitcher appearances '+fid)
   for z in q['raw_features'][side+'_lineup_strength']['value'] or []:
    if len(z.get('history') or [])>10:raise ValueError('too many batting appearances '+fid)
 print(json.dumps({'commit':C,'counts':counts,'calculated_model_performance':False,'inputs_are_reconstructed_not_frozen_output':True},sort_keys=True,indent=2))
if __name__=='__main__':main()
