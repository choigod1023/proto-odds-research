"""Poison 2026 target-day/future official statistics in memory, never source bytes."""
import copy,gzip,json,collections
from pathlib import Path
import assemble_paired as a
from history_index import build_fresh_bat,build_fresh_pitch
from paired_core import previous,valid_batter,valid_pitcher

def main():
 prior,games,gmap,rows,base,bh,ph,pit=a.old_inputs();bat=json.loads(a.fixed_file(prior.BAT));root=Path(r'C:\Users\user\Documents\Codex\kbor\experiments\kbo-official-record-gap-20261103');v1={x['fixture_id']:x for x in a.gz((root/'artifacts/normalized-records.jsonl.gz').read_bytes())};v2={x['fixture_id']:x for x in a.gz((root/'v2/artifacts/supplement-v2.jsonl.gz').read_bytes())};src=a.index(a.gz(a.fixed_file(prior.OUT/'fresh-history-2026-official_batting_and_starter.jsonl.gz')));days=collections.defaultdict(list)
 for fid in v1:days[gmap[fid]['date']].append(fid)
 day=next(d for d,ids in sorted(days.items()) if len(ids)>1 and any(i in src for i in ids));fid=next(i for i in days[day] if i in src);future=next(d for d in sorted(days) if d>day);actual=src[fid];oldb=build_fresh_bat(games,bat,v1,v2);oldp=build_fresh_pitch(games,pit,prior.innings_outs,v1,v2)
 one=copy.deepcopy(v1);two=copy.deepcopy(v2)
 for target in days[day]+days[future]:
  r=one[target];s=two[target];r['outcome']={'poisoned_outcome_not_used':True}
  for side in ('home','away'):
   for x,y in zip(r['statistics']['batters'][side],s['batters'][side]):
    if y['status']=='matched':x['AB']=100;x['H']=99;y['official_v1']['AB']=100;y['official_v1']['H']=99;y['daily_official_row']['AB']=100;y['daily_official_row']['H']=99
   for x in r['statistics']['pitchers'][side]:x['outs']=27;x['ER']=99
 newb=build_fresh_bat(games,bat,one,two);newp=build_fresh_pitch(games,pit,prior.innings_outs,one,two);count=0
 for side in ('home','away'):
  for player in actual['features'][side+'_lineup_strength']['value']:
   pid=player['player_id'];assert previous(oldb.get(pid,[]),day,valid_batter)==previous(newb.get(pid,[]),day,valid_batter);count+=1
  pid=actual['official_schedule_pitcher_context']['pitchers'][side]['player_id'];assert previous(oldp.get(pid,[]),day,valid_pitcher)==previous(newp.get(pid,[]),day,valid_pitcher);count+=1
 print(json.dumps({'status':'ok','fixture_id':fid,'same_day_fixtures_poisoned':len(days[day]),'future_fixtures_poisoned':len(days[future]),'selected_player_histories_checked':count}))
if __name__=='__main__':main()
