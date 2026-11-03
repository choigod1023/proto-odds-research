"""Independently verify completed paired input bytes, features and source selectors."""
import collections,json
from datetime import date
import assemble_paired as a
from paired_core import valid_batter,valid_pitcher
from history_index import build_fresh_bat

def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def main():
 m=json.loads((a.HERE/'paired-inputs-manifest.json').read_text(encoding='utf8'));adapter=a.adapter_module();prior,games,gmap,rows,base,bh,ph,pit=a.old_inputs();dates={f:date.fromisoformat(x['date']) for f,x in gmap.items()};ctx={};counts=collections.Counter();selectors={str(x):collections.Counter() for x in (2023,2024,2025,2026)}
 for name in ('train-projected','train-actual','test-projected','test-actual'):
  path=a.HERE/('paired-'+name+'.jsonl.gz');raw=path.read_bytes();assert a.sha(raw)==m['output_sha256'][name];ctx[name]=a.gz(raw)
 assert len(ctx['train-projected'])==len(ctx['train-actual'])==2182 and len(ctx['test-projected'])==len(ctx['test-actual'])==709
 assert [x['fixture_id'] for x in ctx['train-projected']]==[x['fixture_id'] for x in ctx['train-actual']] and [x['fixture_id'] for x in ctx['test-projected']]==[x['fixture_id'] for x in ctx['test-actual']]
 excluded={x['fixture_id'] for x in m['excluded_fixtures']};assert len(excluded)==m['excluded_fixture_count']==5 and not excluded&{x['fixture_id'] for x in ctx['train-projected']}
 root=a.KBO/'experiments/kbo-official-record-gap-20261103';commit='d11367b7b37f3ea34f4c08987ca042af9235575e'
 def fixedgap(rel,want):return a.index(a.gz(a.pin(a.KBO,commit,(root/rel).relative_to(a.KBO).as_posix(),want)))
 v1=fixedgap('artifacts/normalized-records.jsonl.gz','e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334');v2=fixedgap('v2/artifacts/supplement-v2.jsonl.gz','0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84');bat=json.loads(a.fixed_file(prior.BAT));freshb=build_fresh_bat(games,bat,v1,v2);freshp=a.fresh_pitch(prior,games,pit)
 fresh_base=a.index(a.gz(a.fixed_file(prior.OUT/'fresh-history-2026-projected.jsonl.gz')))
 for stage in ('train','test'):
  for projected,actual in zip(ctx[stage+'-projected'],ctx[stage+'-actual']):
   fid=projected['fixture_id'];day=gmap[fid]['date'];raw=rows[fid];year=raw['season'];expected=base[fid] if stage=='train' else fresh_base[fid];assert projected['raw_features']==expected['features'];assert projected['event_date']==actual['event_date']==day and projected['season']==actual['season']==year
   assert (projected['home_id'],projected['away_id'])==(actual['home_id'],actual['away_id'])==(raw['home_id'],raw['away_id']);assert projected['historical_receipt_proven'] is False and actual['historical_receipt_proven'] is False
   for row in (projected,actual):
    adapter.check_nested(row['raw_features'],fid,dates[fid],dates);assert set(row['features'])==set(row['raw_features'])
    for key,entry in row['raw_features'].items():assert adapter.scalar(key,entry['value'])==row['features'][key]['value']
   for key in projected['raw_features']:
    if not key.endswith(('lineup_strength','starter_strength')):assert projected['raw_features'][key]==actual['raw_features'][key]
   for side in ('home','away'):
    before=projected['raw_features'][side+'_lineup_strength']['value'] or [];after=actual['raw_features'][side+'_lineup_strength']['value'];assert after is not None and [z['player_id'] for z in after]==actual['roster'][side]['hitters']
    b={z['player_id']:z['history'] for z in before};v={z['player_id']:z['history'] for z in after}
    for pid in set(b)&set(v):assert canon(b[pid])==canon(v[pid]);counts['shared_batter_histories']+=1
    for rowlist,index in ((before,freshb if stage=='test' else bh),(after,freshb if stage=='test' else bh)):
     for player in rowlist:
      h=[z for z in index.get(player['player_id'],[]) if z['date']<day and valid_batter(z)][-10:];assert (player['history'] or [])==h
      counts['prior_history_uses_excluded_fixture']+=sum(z['fixture_id'] in excluded for z in h)
    for obj in (projected,actual):
     entry=obj['raw_features'][side+'_starter_strength']['value'];pid=obj['roster'][side]['starter'];index=freshp if stage=='test' else ph;h=[z for z in index.get(pid,[]) if z['date']<day and valid_pitcher(z)][-10:] if pid else []
     if h:assert entry and entry['projected_player_id']==pid and entry['appearances']==h
     else:assert entry is None
     counts['prior_history_uses_excluded_fixture']+=sum(z['fixture_id'] in excluded for z in h)
    ps=projected['roster'][side];ac=actual['roster'][side];s=selectors[str(year)]
    if ps['hitters'] is None:s['lineup_missing_sides']+=1
    else:s['lineup_comparable_sides']+=1;s['lineup_same_set_sides']+=int(set(ps['hitters'])==set(ac['hitters']));s['lineup_same_order_sides']+=int(ps['hitters']==ac['hitters'])
    if ps['starter'] is None:s['starter_missing_sides']+=1
    else:s['starter_comparable_sides']+=1;s['starter_same_id_sides']+=int(ps['starter']==ac['starter'])
   for component in ('hitters','starter'):
    vals=[projected['roster'][side][component] for side in ('home','away')]
    if None not in vals:
     s=selectors[str(year)];key='lineup' if component=='hitters' else 'starter';s[key+'_comparable_games']+=1;s[key+('_same_order_both_sides_games' if key=='lineup' else '_same_id_both_sides_games')]+=int(all(projected['roster'][side][component]==actual['roster'][side][component] for side in ('home','away')))
   counts[stage+'_pairs']+=1
 for year,observed in selectors.items():
  expected=m['selector_match_counts_by_year'][year]
  assert all(observed[k]==expected.get(k,0) for k in set(observed)|set(expected))
 assert m['source_rosters']['2024']['request_spacing_violations_under_1_5_seconds']==128
 assert counts['prior_history_uses_excluded_fixture']>0
 print(json.dumps({'status':'ok',**counts,'excluded_fixture_count':len(excluded),'source_2024_spacing_violations':128}))
if __name__=='__main__':main()
