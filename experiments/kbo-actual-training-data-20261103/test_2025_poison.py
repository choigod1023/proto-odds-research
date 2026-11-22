"""Check 2025 target-day and future statistics cannot alter either paired input."""
import collections,json
import assemble_paired as a
from history_index import build_old
from paired_core import training_pair

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=a.old_inputs();bat=json.loads(a.fixed_file(prior.BAT));root=r'C:\Users\user\Documents\Codex\kbt25';commit='11aa00812d45fdcf35e462413acfc48935dd530d';rel='experiments/kbo-actual-lineups-2025-20261103/actual-lineups-2025.jsonl.gz';payload=a.pin(root,commit,rel,'dc7e9b3b539a8e34e2023240990c966946f4e42ae242c515b5efb63d0a0620c5');entries,_=a.adapt_2025(root,commit,rel,payload);rosters={r['fixture_id']:r for r in entries};byday=collections.defaultdict(list)
 for g in games:
  if g['date'].startswith('2025'):byday[g['date']].append(g['source_fixture_id'])
 day=next(d for d,ids in sorted(byday.items()) if len(ids)>1 and all(f in bat and f in pit for f in ids) and any(rosters[f]['status']=='complete' for f in ids));fids=byday[day];fid=next(f for f in fids if rosters[f]['status']=='complete');row=rows[fid];raw={'fixture_id':fid,'event_date':day,'season':2025,'home_id':row['home_id'],'away_id':row['away_id']};expected=training_pair(baseline[fid],raw,rosters[fid],bh,ph,adapter)
 future=next(g['source_fixture_id'] for g in games if g['date']>day and g['source_fixture_id'] in bat and g['source_fixture_id'] in pit)
 for ident in fids+[future]:
  for side in ('home','away'):
   for x in (bat.get(ident) or {}).get('data',{}).get(side,[]):x.update(ab=100,hit=99,bb=99)
   for x in (pit.get(ident) or {}).get('data',{}).get(side,[]):x.update(inn='9',er=99)
 fb,fp=build_old(games,bat,pit,prior.innings_outs);observed=training_pair(baseline[fid],raw,rosters[fid],fb,fp,adapter);assert expected==observed
 print(json.dumps({'status':'ok','fixture_id':fid,'same_day_fixtures_poisoned':len(fids),'future_fixtures_poisoned':1,'both_paired_inputs_unchanged':True}))
if __name__=='__main__':main()
