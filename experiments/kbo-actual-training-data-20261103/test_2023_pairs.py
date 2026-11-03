"""Rebuild all finalized 2023 matched input pairs without writing or reading labels."""
import collections,json
import assemble_paired as a
from paired_core import training_pair
from pathlib import Path

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,base,bh,ph,pit=a.old_inputs();path=a.HERE/'actual-rosters-2023.jsonl.gz';rosters=a.index(a.gz(path.read_bytes()));assert len(rosters)==723
 counts=collections.Counter()
 for fid,roster in rosters.items():
  if roster['status']!='complete':counts['excluded']+=1;continue
  raw=rows[fid];day=gmap[fid]['date'];model={'fixture_id':fid,'event_date':day,'season':2023,'home_id':raw['home_id'],'away_id':raw['away_id']};projected,actual=training_pair(base[fid],model,roster,bh,ph,adapter)
  assert projected['fixture_id']==actual['fixture_id'] and set(projected['features'])==set(actual['features']);counts['matched_pairs']+=1
  for side in ('home','away'):
   feature=actual['raw_features'][side+'_lineup_strength']['value'];counts['hitter_slots']+=len(feature)
   for player in feature:
    hist=player['history'] or [];assert all(x['date']<day and x['fixture_id']!=fid for x in hist);counts['missing_hitter_history']+=int(not hist)
   pitch=actual['raw_features'][side+'_starter_strength']['value'];counts['missing_pitcher_history']+=int(pitch is None)
   if pitch:assert all(x['date']<day and x['fixture_id']!=fid for x in pitch['appearances'])
 assert counts['matched_pairs']==723;print(json.dumps({'status':'ok',**counts}))
if __name__=='__main__':main()
