"""Independent comparison with the retained upstream files; read-only."""
import collections,json
import run as m
p=m.P
manifest=json.loads((p/'manifest.json').read_text(encoding='utf8'))
paths={k:m.Path(v['path']) for k,v in manifest['sources'].items()}
for k,path in paths.items():
 if m.sha(path)!=manifest['sources'][k]['sha256']:raise ValueError('source hash mismatch: '+k)
rows=m.readgz(p/'inputs.jsonl.gz');raw=m.readgz(paths['rows']);source={x['fixture_id']:x for x in raw}
scores=collections.defaultdict(list)
for x in m.readgz(paths['scores']):scores[x.get('source_fixture_id')].append(x)
ledgers=collections.defaultdict(list)
for x in json.loads(paths['prices'].read_text(encoding='utf8'))['ledgers']['extended_day_team_source_confirmed']:ledgers[x['fixture_id']].append(x)
eval_rows=[x for x in rows if x['date']>='2026-01-01'];priced=[x for x in eval_rows if x['price']];missing=[x for x in priced if x['existing_p'] is None]
for x in eval_rows:
 g=scores[x['id']];assert len(g)==1 and (g[0]['hg'],g[0]['ag'])==(x['home_runs'],x['away_runs'])
for x in priced:
 z=ledgers[x['id']];assert len(z)==1 and m.compatible_price(source[x['id']],scores[x['id']][0],z[0])
 ps=z[0]['price_source'];assert ps['market_id']==x['price']['id'] and all(ps['prices'][k]==x['price'][k] for k in ('home','away'))
result={'source_hashes_match':True,'score_matches':len(eval_rows),'price_source_gate_matches':len(priced),'missing_existing_n':len(missing),'missing_existing_null_kickoff':sum(source[x['id']]['kickoff_at'] is None for x in missing)}
(p/'source-audit.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8');print(json.dumps(result))
