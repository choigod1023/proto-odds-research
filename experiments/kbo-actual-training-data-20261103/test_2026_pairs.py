"""Reconstruct verified 2026 evaluation inputs without labels or predictions."""
import copy,json,collections
from datetime import date
import assemble_paired as a
from paired_core import previous,valid_pitcher,value,roles_projected,model_row,verify_shared_histories

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=a.old_inputs();dates={fid:date.fromisoformat(g['date']) for fid,g in gmap.items()};history=a.fresh_pitch(prior,games,pit)
 path=a.HERE/'actual-starters-2026.jsonl.gz';raw=a.pin(a.HERE.parents[1],'c4e6bdb',path.relative_to(a.HERE.parents[1]).as_posix(),'ca1d6dbc1f301ee4681237cbf1cd864df5079a3ab9364c2f7b50129a9574a69d');veri=a.index(a.gz(raw));groups=json.loads(a.fixed_file(prior.OUT/'official-full-2026-cohorts.jsonl'));ids=groups['all'];sources={name:a.index(a.gz(a.fixed_file(prior.OUT/('fresh-history-2026-'+name+'.jsonl.gz')))) for name in ('projected','official_batting_and_starter')}
 assert len(ids)==709 and set(veri)==set(ids);counts=collections.Counter()
 for fid in ids:
  v=veri[fid]
  if v['status']!='complete':counts['excluded']+=1;continue
  original=sources['projected'][fid];actual=copy.deepcopy(sources['official_batting_and_starter'][fid]);day=gmap[fid]['date'];row=rows[fid]
  assert v['official_game_id']==actual['official_lineup_context']['official_game_id'];roles={}
  for side in ('home','away'):
   p=v['starters'][side];assert p['actual_record_verified'] and str(v['teams'][side])==str(actual['official_lineup_context'][side+'_team_id']);line=actual['official_lineup_context']['lineups'][side];assert [x['order'] for x in line]==list(range(1,10));players=[x['player_id'] for x in line];assert [x['player_id'] for x in actual['features'][side+'_lineup_strength']['value']]==players
   pid=p['player_id'];oldid=actual['official_schedule_pitcher_context']['pitchers'][side]['player_id'];h=previous(history.get(pid,[]),day,valid_pitcher);old=actual['features'][side+'_starter_strength']['value']
   if pid==oldid:assert h==(old or {}).get('appearances',[])
   actual['features'][side+'_starter_strength']=value({'projected_player_id':pid,'appearances':copy.deepcopy(h)} if h else None,h);roles[side]={'hitters':players,'starter':pid};counts['schedule_id_changed']+=int(pid!=oldid);counts['pitcher_history_missing']+=int(not h)
  for key in original['features']:
   if not key.endswith(('lineup_strength','starter_strength')):assert original['features'][key]==actual['features'][key]
  verify_shared_histories(original,actual)
  adapter.check_nested(original['features'],fid,dates[fid],dates);adapter.check_nested(actual['features'],fid,dates[fid],dates);raw={'fixture_id':fid,'event_date':day,'season':2026,'home_id':row['home_id'],'away_id':row['away_id']};p=model_row(original,raw,roles_projected(original),adapter);q=model_row(actual,raw,roles,adapter);assert p['fixture_id']==q['fixture_id'];counts['paired_games']+=1
 assert counts['paired_games']==709;print(json.dumps({'status':'ok',**counts}))
if __name__=='__main__':main()
