"""Verify the 2024 resumed-game alias has no target or future stats in paired inputs."""
import json
import assemble_paired as a
from history_index import build_old
from paired_core import training_pair

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=a.old_inputs();bat=json.loads(a.fixed_file(prior.BAT));root=r'C:\Users\user\Documents\Codex\kbt24';commit='e8ba21f970f73594848e75cc6515a1dd3a994bf1';rel='experiments/kbo-actual-2024-20261103/rosters.jsonl';payload=a.pin(root,commit,rel,'2c56d0139dd6de89777aefe9e74d3e925af5bb9dafc3d4603c68f44fd640d485');entries,_=a.adapt_2024(root,commit,rel,payload);rosters={r['fixture_id']:r for r in entries};fid='77771023SSHT02024';day=gmap[fid]['date'];assert day=='2024-10-23';row=rows[fid];raw={'fixture_id':fid,'event_date':day,'season':2024,'home_id':row['home_id'],'away_id':row['away_id']};expected=training_pair(baseline[fid],raw,rosters[fid],bh,ph,adapter)
 future=next(g['source_fixture_id'] for g in games if g['date']>day and g['source_fixture_id'] in bat and g['source_fixture_id'] in pit)
 for ident in (fid,future):
  for side in ('home','away'):
   for x in (bat.get(ident) or {}).get('data',{}).get(side,[]):x.update(ab=100,hit=99,bb=99)
   for x in (pit.get(ident) or {}).get('data',{}).get(side,[]):x.update(inn='9',er=99)
 fb,fp=build_old(games,bat,pit,prior.innings_outs);observed=training_pair(baseline[fid],raw,rosters[fid],fb,fp,adapter);assert expected==observed
 print(json.dumps({'status':'ok','fixture_id':fid,'target_and_future_poisoned':True,'both_paired_inputs_unchanged':True}))
if __name__=='__main__':main()
