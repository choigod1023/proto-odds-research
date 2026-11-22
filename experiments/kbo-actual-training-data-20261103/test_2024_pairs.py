"""Rebuild every 2024 matched input pair from its finalized producer commit."""
import collections,json
import assemble_paired as a
from paired_core import training_pair

def main():
 adapter=a.adapter_module();prior,games,gmap,rows,base,bh,ph,pit=a.old_inputs();root=r'C:\Users\user\Documents\Codex\kbt24';commit='e8ba21f970f73594848e75cc6515a1dd3a994bf1';rel='experiments/kbo-actual-2024-20261103/rosters.jsonl';data=a.pin(root,commit,rel,'2c56d0139dd6de89777aefe9e74d3e925af5bb9dafc3d4603c68f44fd640d485');rosters,meta=a.adapt_2024(root,commit,rel,data);counts=collections.Counter()
 for roster in rosters:
  if roster['status']!='complete':counts['excluded']+=1;continue
  fid=roster['fixture_id'];raw=rows[fid];day=gmap[fid]['date'];model={'fixture_id':fid,'event_date':day,'season':2024,'home_id':raw['home_id'],'away_id':raw['away_id']};p,q=training_pair(base[fid],model,roster,bh,ph,adapter);assert p['fixture_id']==q['fixture_id'];a.compare_selectors(counts,p['roster'],q['roster']);counts['paired_games']+=1
  for side in ('home','away'):
   for player in q['raw_features'][side+'_lineup_strength']['value']:
    h=player['history'] or [];assert all(x['date']<day and x['fixture_id']!=fid for x in h);counts['missing_hitter_history']+=int(not h)
   h=q['raw_features'][side+'_starter_strength']['value'];counts['missing_pitcher_history']+=int(h is None)
 assert counts['paired_games']==728 and not counts['excluded'];assert meta['request_spacing_violations_under_1_5_seconds']==128;print(json.dumps({'status':'ok',**counts,'source_spacing_violations':128}))
if __name__=='__main__':main()
