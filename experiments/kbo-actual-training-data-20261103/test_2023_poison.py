"""Poison current, same-day and future player statistics in memory only."""
import copy,json
import assemble_paired as a
from history_index import build_old
from paired_core import training_pair

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=a.old_inputs();bat=json.loads(a.fixed_file(prior.BAT));rosters=a.index(a.gz((a.HERE/'actual-rosters-2023.jsonl.gz').read_bytes()))
 byday={}
 for g in games:
  if g['date'].startswith('2023'):byday.setdefault(g['date'],[]).append(g['source_fixture_id'])
 day=next(d for d,ids in sorted(byday.items()) if len(ids)>1 and all(i in bat and i in pit for i in ids));fids=byday[day];fid=fids[0];future=next(g['source_fixture_id'] for g in games if g['date']>day and g['source_fixture_id'] in bat and g['source_fixture_id'] in pit)
 row=rows[fid];raw={'fixture_id':fid,'event_date':day,'season':2023,'home_id':row['home_id'],'away_id':row['away_id']};expected=training_pair(baseline[fid],raw,rosters[fid],bh,ph,adapter)
 for ident in fids+[future]:
  for side in ('home','away'):
   for x in (bat.get(ident) or {}).get('data',{}).get(side,[]):x.update(ab=100,hit=99,bb=99)
   for x in (pit.get(ident) or {}).get('data',{}).get(side,[]):x.update(inn='9',er=99)
 fresh_b,fresh_p=build_old(games,bat,pit,prior.innings_outs);observed=training_pair(baseline[fid],raw,rosters[fid],fresh_b,fresh_p,adapter)
 assert expected==observed;print(json.dumps({'status':'ok','fixture_id':fid,'same_day_fixtures_poisoned':len(fids),'future_fixtures_poisoned':1,'selected_identity_unchanged':True,'both_input_pairs_unchanged':True}))
if __name__=='__main__':main()
