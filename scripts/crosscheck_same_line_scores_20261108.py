import collections, datetime as dt, gzip, hashlib, json
from pathlib import Path
p=Path('C:/Users/user/AppData/Local/Temp/market-movement-20261108/db-proto-score-history-102-132.json.gz')
with gzip.open(p,'rt',encoding='utf8') as f: data=json.load(f)
rows=data['score_rows']; groups=collections.defaultdict(list)
for row in rows: groups[(row['source'],row['season'],row['round'],str(row['game_no']))].append(row)
latest=[]
for values in groups.values():
 t=max(x['observed_at'] for x in values); v=[x for x in values if x['observed_at']==t]
 if len({json.dumps(x,sort_keys=True,ensure_ascii=False) for x in v})==1:latest.append(v[0])
bykey={(r['season'],r['round'],str(r['game_no'])):r for r in latest}
physical=collections.defaultdict(set)
for r in latest:
 if r['result_state']=='confirmed':physical[(r['sport'],r['league'],r['home_team'],r['away_team'],r['kickoff'])].add((r['home_score'],r['away_score']))
x=json.loads(Path('experiments/same-line-market-evidence-20261108/direction-results.json').read_text(encoding='utf8'))
c=collections.Counter();details=[]
for v in x['candidates']:
 c['comparison_events']+=1;r=bykey.get((2026,v['round'],str(v['game_no'])))
 if not r:continue
 c['latest_product_record']+=1
 if r['result_state']!='confirmed':c['latest_not_confirmed']+=1;continue
 c['latest_confirmed']+=1
 k=v['fixture_key']; kick=dt.datetime.fromisoformat(v['kickoff'].replace('Z','+00:00')).astimezone(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None).isoformat(timespec='seconds')
 if (r['sport'],r['league'],r['home_team'],r['away_team'],r['kickoff'])!=(k[1],k[2],k[3],k[4],kick):continue
 c['exact_fixture_confirmed']+=1
 scores=physical[(r['sport'],r['league'],r['home_team'],r['away_team'],r['kickoff'])]
 if len(scores)!=1:c['conflicting_latest_physical_scores']+=1;continue
 c['exact_fixture_and_physical_score_agreement']+=1
 details.append({'round':v['round'],'game_no':v['game_no'],'observed_at':r['observed_at'],'score':[r['home_score'],r['away_score']]})
out={'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'meta':data['meta'],'counts':dict(c),'details':details,'limitation':'Score only: period and official market settlement rules not proven; no score-derived grade used.'}
Path('experiments/same-line-market-evidence-20261108/score-crosscheck.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k!='details'},ensure_ascii=False,indent=2))
