import collections,gzip,json,pathlib,hashlib
p=pathlib.Path('C:/Users/user/AppData/Local/Temp/market-movement-20261108/db-changed-products-132.json.gz')
with gzip.open(p,'rt',encoding='utf8') as f: d=json.load(f)
r=json.loads(pathlib.Path('experiments/same-line-market-evidence-20261108/direction-results.json').read_text(encoding='utf8'));cs=[x for x in r['candidates'] if x['round']==132]; c=collections.Counter()
for x in cs:
 c['round_132_candidates']+=1; vals=[]
 for state in d['states']:
  row=state['row']; key=x['fixture_key'];
  if str(row.get('game_no'))!=str(x['game_no']) or row.get('round')!=x['round']:continue
  if (row.get('sport'),row.get('league'),row.get('home'),row.get('away'),row.get('date_text'),row.get('market_family'),row.get('market_label')) != (key[1],key[2],key[3],key[4],key[5],x['market'],x['label']):continue
  odds=json.loads(row['odds_json'])
  for obs in state['observations']:vals.append((obs['observed_at'],odds))
 if vals:c['matching_product_identity']+=1
 if (x['first_time'],x['first_odds']) in vals:c['first_exact_time_and_odds']+=1
 if (x['last_time'],x['last_odds']) in vals:c['last_exact_time_and_odds']+=1
 if (x['first_time'],x['first_odds']) in vals and (x['last_time'],x['last_odds']) in vals:c['both_exact']+=1
out={'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'meta':d['meta'],'counts':dict(c),'meaning':'Exact identity, timestamp and odds match only; truncated sample, no HTTP proof.'}
pathlib.Path('experiments/same-line-market-evidence-20261108/db-crosscheck.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8'); print(json.dumps(out,ensure_ascii=False,indent=2))
