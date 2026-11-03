"""Read-only reconciliation of exported raw-quote/label artifacts and frozen sources."""
import collections,csv,gzip,hashlib,json,math,pathlib,re,subprocess
import build_inputs as b
H=pathlib.Path(__file__).resolve().parent

def load(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return list(map(json.loads,f))
def main():
 manifest=json.loads((H/'manifest.json').read_text(encoding='utf8'));raw={x['fixture_id']:x for x in load(b.EFFECTS/'experiments/kbo-player-effects-20261103/inputs/rows.jsonl.gz')};labels={x['fixture_id']:x for x in load(b.EFFECTS/'experiments/kbo-player-effects-20261103/inputs/development-labels.jsonl.gz')};common={x['fixture_id']:x for x in load(b.EXP/'vendor/evolution/common/inputs/development-labels.jsonl.gz')};actual=[x for x in load(b.EXP/'vendor/evolution/common/inputs/train-actual.jsonl.gz') if x['season'] in (2024,2025)];stats={};byyear={y:{x['fixture_id']:x for x in actual if x['season']==y} for y in (2024,2025)}
 source_builder=manifest['original_price_builder'];builder_blob=subprocess.check_output(['git','cat-file','blob',source_builder['commit']+':'+source_builder['path']],cwd=b.KBODATA);assert hashlib.sha256(builder_blob).hexdigest()==source_builder['git_blob_sha256'] and b"r['is_void']!='False'" in builder_blob and b"(m['home_score'],m['away_score'])!=(g['hg'],g['ag'])" in builder_blob
 for rel,meta in manifest['source_pins'].items():
  if rel=='domestic_market_csv':continue
  root=b.R24 if rel.startswith('experiments/kbo-actual-2024-') else b.R25 if rel.startswith('experiments/kbo-actual-lineups-2025-') else b.ROOT if rel.startswith('experiments/kbo-m0-followup-') else b.EFFECTS
  blob=subprocess.check_output(['git','cat-file','blob',meta['commit']+':'+rel],cwd=root);assert hashlib.sha256(blob).hexdigest()==meta['sha256'] and (root/rel).read_bytes()==blob
 r24={x['fixture_id']:x for x in map(json.loads,(b.R24/'experiments/kbo-actual-2024-20261103/rosters.jsonl').read_text(encoding='utf8').splitlines())};r25={x['fixture_id']:x for x in load(b.R25/'experiments/kbo-actual-lineups-2025-20261103/actual-lineups-2025.jsonl.gz')}
 assert set(byyear[2024])==set(r24) and set(byyear[2025])=={k for k,v in r25.items() if v['complete_verified'] is True}
 cache24=b.load_schedule24((b.R24/'experiments/kbo-actual-2024-20261103/official-evidence.tar.gz').read_bytes(),{x['evidence']['schedule_evidence']['sha256'] for x in r24.values()});cache25=b.load_schedule25((b.R25/'experiments/kbo-actual-lineups-2025-20261103/raw-evidence.jsonl.gz').read_bytes(),{x['schedule_evidence']['sha256'] for x in r25.values()})
 archived={x['source_fixture_id']:x for x in load(b.EFFECTS/'data/model_candidates/kbo/archived-reproduction.jsonl.gz') if x.get('role')=='validation' and str(x.get('date','')).startswith('2024')};assert len(archived)==712
 csv_bytes=b.CSV.read_bytes();assert hashlib.sha256(csv_bytes).hexdigest()==manifest['source_pins']['domestic_market_csv']['sha256'];quotes=collections.defaultdict(list)
 with b.CSV.open(encoding='utf-8-sig',newline='') as f:
  for row in csv.DictReader(f):
   if row.get('year') in ('2024','2025') and row.get('league')=='KBO' and row.get('sport')=='bs' and row.get('market_family')=='승패' and row.get('market_tag')=='hm' and row.get('n_way')=='2' and not row.get('market_label'):
    quotes[f"proto:{row['year']}:{row['round']}:{row['game_no']}:full_game_2way"].append(row)
 for y in (2024,2025):
  def read(label):
   p=H/f'{label}-{y}.jsonl.gz';rawbytes=p.read_bytes();assert hashlib.sha256(rawbytes).hexdigest()==manifest['outputs_sha256'][f'{label}-{y}'];return load(p)
  prices=read('prices');ys=read('labels');excluded=read('exclusions');target=byyear[y];q={z['fixture_id']:z for z in prices};l={z['fixture_id']:z for z in ys};e={z['fixture_id']:z for z in excluded}
  assert len(q)==len(prices) and len(l)==len(ys) and len(e)==len(excluded) and set(q).isdisjoint(e) and set(q)|set(e)==set(target) and set(l)==set(target)
  assert len(q)==manifest['population'][str(y)]['prices_eligible'] and len(e)==manifest['population'][str(y)]['excluded_unique_fixtures']
  gids=set();official_games={}
  for fid,x in target.items():
   source=(r24 if y==2024 else r25)[fid];se=source['evidence']['schedule_evidence'] if y==2024 else source['schedule_evidence'];schedule=(cache24 if y==2024 else cache25)[se['sha256']];matches=[g for g in schedule['game'] if g['G_ID']==source['official_game_id']];assert len(matches)==1;g=matches[0];assert g['G_ID'] not in gids;gids.add(g['G_ID']);official_games[fid]=g
   assert (g['G_DT'],g['HOME_NM'].strip(),g['AWAY_NM'].strip())==(x['event_date'].replace('-',''),x['home_id'],x['away_id']) and str(g['GAME_RESULT_CK'])=='1' and str(g['GAME_STATE_SC'])=='3'
   z=l[fid];assert z==labels[fid]==common[fid] and (int(g['B_SCORE_CN']),int(g['T_SCORE_CN']))==(z['home_score'],z['away_score'])
  ids=set();corrob=0;wins=collections.Counter()
  for fid,price in q.items():
   x=target[fid];r=raw[fid];z=l[fid];src=price['price_source'];assert z==labels[fid]==common[fid] and z['status']=='final';assert (price['date'],price['home_id'],price['away_id'])==(x['event_date'],x['home_id'],x['away_id']) and r['kickoff_at'][11:16]==str(official_games[fid]['G_TM'])
   market=r['market'];assert market is not None and src['market_id']==market['market_id']==z['market_id'] and src['kind']==market['kind']=='full_game_2way' and src['settlement_rule']==market['settlement_rule']=='draw_void_refund';assert src['prices']==market['selections'];assert all(type(v) in (int,float) and math.isfinite(v) and v>1 for v in src['prices'].values());assert src['odds_observed_at'] is None and src['historical_quote_receipt_proven'] is False and src['execution_price_verified'] is False
   assert src['market_id'] not in ids;ids.add(src['market_id']);assert len(quotes[src['market_id']])==1;cs=quotes[src['market_id']][0];assert cs['booking_class']=='2-way' and cs['market_type']=='승패(2-way)' and cs['is_void']=='False';pr=[float(v) for v in cs['odds'].split(',')];assert pr==[src['prices']['home'],src['prices']['away']]
   h=re.fullmatch(r'(.+?)\s+(\d+)',cs['home']);aw=re.fullmatch(r'(\d+)\s+(.+)',cs['away']);assert h and aw and (h[1],aw[2],int(h[2]),int(aw[1]))==(x['home_id'],x['away_id'],z['home_score'],z['away_score'])
   assert price['winner']==('home' if z['home_score']>z['away_score'] else 'away' if z['home_score']<z['away_score'] else 'draw');wins[price['winner']]+=1
   assert src['provenance']['source_csv_sha256_verified'] is True and src['provenance']['source_csv_quote_exactly_matched'] is True
   assert price['result_evidence']['historical_receipt_proven'] is False
   if src['corroboration']:
    assert y==2024 and src['corroboration']['both_prices_equal'] is True and src['corroboration']['used_for_substitution'] is False
    zrep=archived[fid];assert (zrep['date'],zrep['home'],zrep['away'],zrep['hg'],zrep['ag'])==(x['event_date'],x['home_id'],x['away_id'],z['home_score'],z['away_score']) and zrep['order']==['home','away'] and zrep['prices']==[src['prices']['home'],src['prices']['away']];corrob+=1
  for fid in e:assert e[fid]['reasons']==['market_missing'] and raw[fid]['market'] is None
  draws=sum(z['home_score']==z['away_score'] for z in ys);eligible_draws=wins['draw'];assert draws==manifest['population'][str(y)]['corroboration']['label_draws'] and eligible_draws==manifest['population'][str(y)]['corroboration']['price_eligible_draws']
  if y==2024:assert corrob==712
  stats[y]={'target':len(target),'labels':len(ys),'prices':len(prices),'missing_prices':len(excluded),'all_label_draws':draws,'price_draws':eligible_draws,'corroborated_prices':corrob}
 print(json.dumps({'status':'ok','years':stats,'no_performance_scored':True},ensure_ascii=False))
if __name__=='__main__':main()
