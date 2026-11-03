"""Validate frozen 2024/25 raw quotes and official results; no scoring or collection."""
import base64,collections,csv,gzip,hashlib,json,math,os,re,subprocess,tarfile
from datetime import date,datetime,timedelta
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];EXP=HERE.parent
EFFECTS=Path(r'C:\Users\user\Documents\Codex\kbeffects');R24=Path(r'C:\Users\user\Documents\Codex\kbt24');R25=Path(r'C:\Users\user\Documents\Codex\kbt25')
KBODATA=Path(r'C:\Users\user\Documents\Codex\kbodata')
COMMITS={'effects':'d08ccb708033fb6b067e84e80eae81ea3c9b4cfa','archive':'66f1c255b41714dabc6dc0638bbd8f1e12e13006','rosters2024':'e8ba21f970f73594848e75cc6515a1dd3a994bf1','rosters2025':'11aa00812d45fdcf35e462413acfc48935dd530d','contract':'0fe05367fc85e71f2df2784c6ddf50fb017bcba2'}
PINS={}
CSV=Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\internal-game-factors\data\processed\games.csv.tmp')
def sha(b):return hashlib.sha256(b).hexdigest()
def pinned(root,commit,rel,want=None):
 path=root/rel;raw=path.read_bytes();blob=subprocess.check_output(['git','cat-file','blob',commit+':'+Path(rel).as_posix()],cwd=root)
 if raw!=blob or (want and sha(raw)!=want):raise ValueError('git_blob_or_sha_mismatch:'+str(rel))
 PINS[str(rel)]={'commit':commit,'sha256':sha(raw),'exact_worktree_git_blob':True};return raw
def gzrows(raw):return [json.loads(x) for x in gzip.decompress(raw).splitlines()]
def index(rows,key='fixture_id'):
 out={}
 for row in rows:
  ident=row[key]
  if ident in out:raise ValueError('duplicate_'+key+':'+ident)
  out[ident]=row
 return out
def save(name,rows):
 path=HERE/name;tmp=path.with_name(path.name+'.tmp')
 with tmp.open('wb') as f:
  with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
   for row in rows:z.write((json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8'))
 os.replace(tmp,path);return sha(path.read_bytes())
def valid_price(x):return type(x) in (int,float) and math.isfinite(x) and x>1

def load_market_csv():
 import io
 raw=CSV.read_bytes();digest=sha(raw);expected='9ffddb2dcbc4297c32e6ad3826f392e1842e52e10b509e4cbe47eed1f84b9da4'
 if digest!=expected:raise ValueError('domestic_market_csv_sha_mismatch')
 PINS['domestic_market_csv']={'sha256':digest,'exact_recorded_source_sha':True,'git_blob_witness':False}
 out=collections.defaultdict(list)
 with io.StringIO(raw.decode('utf-8-sig'),newline='') as f:
  for row in csv.DictReader(f):
   if row.get('year') not in ('2024','2025') or row.get('league')!='KBO' or row.get('sport')!='bs' or row.get('market_family')!='승패' or row.get('market_tag')!='hm' or row.get('n_way')!='2' or row.get('market_label'):continue
   key=f"proto:{row['year']}:{row['round']}:{row['game_no']}:full_game_2way";out[key].append(row)
 return out

def csv_quote_reasons(rows,market,day,home,away,label,kickoff):
 if len(rows)!=1:return ['raw_quote_source_missing' if not rows else 'raw_quote_source_duplicate']
 row=rows[0];reasons=[]
 if row.get('market_type')!='승패(2-way)' or row.get('booking_class')!='2-way':reasons.append('raw_quote_product_conflict')
 h=re.fullmatch(r'(.+?)\s+(\d+)',row.get('home',''));a=re.fullmatch(r'(\d+)\s+(.+)',row.get('away',''));d=re.fullmatch(r'(\d\d)\.(\d\d)\([^)]*\)\s+(\d\d):(\d\d)',row.get('date_text',''))
 if not h or not a or not d:return ['raw_quote_source_parse_invalid']
 if (h[1],a[2])!=(home,away):reasons.append('raw_quote_team_conflict')
 if (int(h[2]),int(a[1]))!=(label['home_score'],label['away_score']):reasons.append('raw_quote_score_conflict')
 try:
  quoted=datetime(int(row['year']),*[int(x) for x in d.groups()]);archived=datetime.fromisoformat(kickoff) if kickoff else None
  if archived and archived.utcoffset()!=timedelta(hours=9):reasons.append('raw_quote_kickoff_timezone_conflict')
  if quoted.date().isoformat()!=day or not archived or quoted!=archived.replace(tzinfo=None):reasons.append('raw_quote_kickoff_conflict')
  prices=[float(v) for v in row['odds'].split(',')]
  if len(prices)!=2 or not all(valid_price(v) for v in prices) or row['is_void']!='False':reasons.append('raw_quote_invalid_or_void')
  elif not isinstance(market.get('selections'),dict) or any(float(market['selections'].get(s,0))!=prices[i] for i,s in enumerate(('home','away'))):reasons.append('raw_quote_prices_conflict')
 except (ValueError,TypeError,OverflowError):reasons.append('raw_quote_source_parse_invalid')
 return reasons

def fixture_check(fid,day,home,away,g):
 if str(g.get('G_DT'))!=day.replace('-','') or (str(g.get('HOME_NM') or '').strip(),str(g.get('AWAY_NM') or '').strip())!=(home,away):raise ValueError('official_date_or_team_conflict')
 gid=g['G_ID'];suffix=str(g.get('HEADER_NO'));expected=day.replace('-','')+str(g['AWAY_ID'])+str(g['HOME_ID'])+suffix
 if gid!=expected:raise ValueError('official_game_id_header_conflict')
 season=day[:4]
 if fid==gid+season:return 'exact_official_game_id'
 if fid.startswith('score:'):
  if fid!=f'score:{day}:{home}:{away}':raise ValueError('score_alias_conflict')
  return 'unique_score_date_team_alias'
 if len(fid)==17 and fid[-4:]==season and fid[4:13]==gid[4:]:return 'encoded_year_official_game_alias'
 raise ValueError('fixture_alias_conflict')

def score_alias_unambiguous(schedule,day,home,away):
 same=[z for z in schedule['game'] if str(z.get('G_DT'))==day.replace('-','') and str(z.get('HOME_NM') or '').strip()==home and str(z.get('AWAY_NM') or '').strip()==away]
 return len(same)==1

def load_schedule24(bundle,wanted):
 out={}
 import io
 with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:gz') as t:
  for member in t:
   if not member.isfile() or not member.name.startswith('raw/') or not member.name.endswith('.gz'):continue
   digest=Path(member.name).name[:-3]
   if digest not in wanted:continue
   raw=gzip.decompress(t.extractfile(member).read())
   if sha(raw)!=digest:raise ValueError('2024_schedule_raw_hash_mismatch')
   if digest in out:raise ValueError('2024_duplicate_schedule_raw')
   out[digest]=json.loads(raw)
 if set(out)!=wanted:raise ValueError('2024_schedule_raw_missing')
 return out

def load_schedule25(bundle,wanted):
 out={}
 for row in gzrows(bundle):
  digest=row['sha256']
  if digest not in wanted:continue
  raw=base64.b64decode(row['body_base64'],validate=True)
  if sha(raw)!=digest:raise ValueError('2025_schedule_raw_hash_mismatch')
  if digest in out and out[digest]!=raw:raise ValueError('2025_duplicate_digest_conflict')
  out[digest]=raw
 if set(out)!=wanted:raise ValueError('2025_schedule_raw_missing')
 return {k:json.loads(v) for k,v in out.items()}

def quote_reasons(market,label,origin):
 if market is None:return ['market_missing']
 reasons=[]
 if not isinstance(market,dict):return ['market_invalid']
 if market.get('kind')!='full_game_2way':reasons.append('market_kind_conflict')
 if market.get('execution_price_verified') is not False:reasons.append('execution_status_conflict')
 if market.get('settlement_rule')!='draw_void_refund' or label.get('settlement_rule')!='draw_void_refund':reasons.append('settlement_rule_conflict')
 ident=market.get('market_id')
 if not isinstance(ident,str) or not ident:reasons.append('market_id_missing')
 elif ident!=label.get('market_id'):reasons.append('market_id_label_conflict')
 prices=market.get('selections')
 if not isinstance(prices,dict) or set(prices)!={'home','away'}:reasons.append('two_sided_price_missing')
 elif not all(valid_price(prices[s]) for s in ('home','away')):reasons.append('two_sided_price_invalid')
 if not origin or origin.get('source_id')!='domestic_market' or not origin.get('sha256'):reasons.append('raw_quote_provenance_missing')
 return reasons

def main():
 contract=pinned(ROOT,COMMITS['contract'],'experiments/kbo-m0-followup-20261103/contract.json','c56b5675def7e54783b6e67c87e09c5d03d405aba90d12fa07b0fb577be8f7ef')
 builder_commit='13291d23e855e0d500327cb39ac64a480e395837';builder_path='experiments/kbo-player-data-20261103/build.py';builder=subprocess.check_output(['git','cat-file','blob',builder_commit+':'+builder_path],cwd=KBODATA)
 if sha(builder)!='5f493bc975276f27cb5ecc0af80ead5f684fd5d8f0054199e8fdad3db23e0c6f' or b"r['is_void']!='False'" not in builder or b"(m['home_score'],m['away_score'])!=(g['hg'],g['ag'])" not in builder:raise ValueError('original_price_builder_policy_mismatch')
 common='experiments/kbo-m0-followup-20261103/vendor/evolution/common/inputs/'
 actual=gzrows(pinned(ROOT,COMMITS['contract'],common+'train-actual.jsonl.gz','0d760f7cc1f993792d8ab5e697a9e77c903e5abc4f90823730baabcdc1c4adf9'))
 common_labels=index(gzrows(pinned(ROOT,COMMITS['contract'],common+'development-labels.jsonl.gz','d55a39c5c8300cff21a4a902a68db4f1a8ad882778633a50df500be4f46ae8bc')))
 base='experiments/kbo-player-effects-20261103/inputs/'
 raw=index(gzrows(pinned(EFFECTS,COMMITS['effects'],base+'rows.jsonl.gz','793f48649b5936d55424e4d48d65166548ffe762fa244381618a27b62e4b1bf2')))
 labels=index(gzrows(pinned(EFFECTS,COMMITS['effects'],base+'development-labels.jsonl.gz','22f3691f1e9017bbcfd9b2bde35ca0f6c208e292f117d056dd5271a80e9f6a33')))
 archive=gzrows(pinned(EFFECTS,COMMITS['archive'],'data/model_candidates/kbo/archived-reproduction.jsonl.gz','4f5bc713a6487585ea5d42aff6371043b55cdaacdef3690c97ea67e54e2c1773'))
 market_csv=load_market_csv()
 games=index(gzrows(pinned(EFFECTS,COMMITS['effects'],'data/model_candidates/kbo/archived-games.jsonl.gz','c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce')),'source_fixture_id')
 path24='experiments/kbo-actual-2024-20261103/';m24=json.loads(pinned(R24,COMMITS['rosters2024'],path24+'manifest.json','87c9def24b8a084423e1645984f2d9be86b041f39e79f5edaacb910eb86009fb'))
 raw24=pinned(R24,COMMITS['rosters2024'],path24+'rosters.jsonl',m24['rosters_sha256']);rosters24=index(map(json.loads,raw24.splitlines()));bundle24=pinned(R24,COMMITS['rosters2024'],path24+'official-evidence.tar.gz',m24['evidence_bundle_sha256'])
 path25='experiments/kbo-actual-lineups-2025-20261103/';m25=json.loads(pinned(R25,COMMITS['rosters2025'],path25+'manifest.json','8e4063a12e885b62fec5c5ebf8e3786ba4b7ae6698adf67f22881f3418f83db2'))
 rosters25=index(gzrows(pinned(R25,COMMITS['rosters2025'],path25+'actual-lineups-2025.jsonl.gz',m25['lineups_gzip_sha256'])));bundle25=pinned(R25,COMMITS['rosters2025'],path25+'raw-evidence.jsonl.gz',m25['evidence_gzip_sha256'])
 if m24['contract_sha256']!=m25['contract_sha256'] or m24['input_sha256']!=m25['source_rows_sha256']:raise ValueError('roster_contract_or_rows_mismatch')
 target={y:[x for x in actual if x['season']==y] for y in (2024,2025)}
 if {y:len(v) for y,v in target.items()}!={2024:728,2025:731}:raise ValueError('fixed_target_population_mismatch')
 for y in (2024,2025):index(target[y])
 if set(x['fixture_id'] for x in target[2024])!=set(rosters24):raise ValueError('2024_cohort_mismatch')
 if set(x['fixture_id'] for x in target[2025])!={k for k,v in rosters25.items() if v['complete_verified'] is True}:raise ValueError('2025_actual_cohort_mismatch')
 sched24=load_schedule24(bundle24,{x['evidence']['schedule_evidence']['sha256'] for x in rosters24.values()});sched25=load_schedule25(bundle25,{x['schedule_evidence']['sha256'] for x in rosters25.values()})
 rep=[x for x in archive if x.get('role')=='validation' and str(x.get('date','')).startswith('2024')]
 if len(rep)!=712:raise ValueError('corroboration_population_mismatch')
 rep=index(rep,'source_fixture_id');out={};labelout={};excluded={};summaries={}
 all_targets=target[2024]+target[2025]
 market_freq=collections.Counter((raw.get(x['fixture_id']) or {}).get('market',{}).get('market_id') for x in all_targets if isinstance((raw.get(x['fixture_id']) or {}).get('market'),dict) and (raw[x['fixture_id']]['market']).get('market_id'))
 official_freq=collections.Counter((rosters24 if x['season']==2024 else rosters25).get(x['fixture_id'],{}).get('official_game_id') for x in all_targets)
 for year in (2024,2025):
  result=[];result_labels=[];ex=[];counts=collections.Counter();coverage=collections.Counter()
  for x in sorted(target[year],key=lambda z:(z['event_date'],z['fixture_id'])):
   fid=x['fixture_id'];day=x['event_date'];home=x['home_id'];away=x['away_id'];date.fromisoformat(day);rr=raw.get(fid);lab=labels.get(fid);cl=common_labels.get(fid);roster=(rosters24 if year==2024 else rosters25).get(fid);gg=games.get(fid);errs=[]
   if not rr or not lab or not cl or not roster or not gg:errs.append('identity_or_label_source_missing')
   if errs:ex.append({'fixture_id':fid,'date':day,'reasons':errs});counts.update(errs);continue
   if (rr['home_id'],rr['away_id'],rr['season'])!=(home,away,year) or (gg['home'],gg['away'],gg['date'])!=(home,away,day):errs.append('archive_team_or_date_conflict')
   if roster.get('date')!=day:errs.append('roster_date_conflict')
   if rr.get('kickoff_at') and rr['kickoff_at'][:10]!=day:errs.append('archive_kickoff_date_conflict')
   if lab!=cl:errs.append('archived_label_conflict')
   if lab.get('status')!='final' or any(type(lab.get(k)) is not int or lab[k]<0 for k in ('home_score','away_score')) or lab.get('assumed_completed_date')!=day:errs.append('archived_label_invalid')
   if (gg.get('hg'),gg.get('ag'))!=(lab.get('home_score'),lab.get('away_score')):errs.append('archive_score_conflict')
   se=roster['evidence']['schedule_evidence'] if year==2024 else roster['schedule_evidence'];schedule=(sched24 if year==2024 else sched25)[se['sha256']]
   try:received=datetime.fromisoformat(se['received_at'].replace('Z','+00:00'))
   except (KeyError,TypeError,ValueError):received=None
   if se.get('http_status')!=200 or se.get('url')!='https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList' or str((se.get('params') or {}).get('date'))!=day.replace('-','') or received is None or received.utcoffset() is None:errs.append('official_schedule_evidence_invalid')
   candidates=[g for g in schedule.get('game',[]) if g.get('G_ID')==roster['official_game_id']]
   if len(candidates)!=1:errs.append('official_game_missing_or_duplicate');g=None
   else:
    g=candidates[0];gid=g['G_ID']
    if official_freq[gid]>1:errs.append('duplicate_official_game_id')
    try:basis=fixture_check(fid,day,home,away,g)
    except ValueError as e:errs.append(str(e));basis=None
    if fid.startswith('score:'):
     if not score_alias_unambiguous(schedule,day,home,away):errs.append('ambiguous_doubleheader_score_alias')
    if str(g.get('SEASON_ID'))!=str(year) or str(g.get('LE_ID'))!='1':errs.append('official_league_or_season_conflict')
    if str(g.get('GAME_STATE_SC'))!='3' or str(g.get('GAME_RESULT_CK'))!='1':errs.append('official_result_not_final')
    if not str(g.get('B_SCORE_CN')).isdigit() or not str(g.get('T_SCORE_CN')).isdigit():errs.append('official_score_missing')
    elif (int(g['B_SCORE_CN']),int(g['T_SCORE_CN']))!=(lab.get('home_score'),lab.get('away_score')):errs.append('official_score_conflict')
   if errs:ex.append({'fixture_id':fid,'date':day,'reasons':sorted(set(errs))});counts.update(set(errs));continue
   result_labels.append(lab);coverage['official_result_verified']+=1
   is_draw=lab['home_score']==lab['away_score'];coverage['label_draws']+=int(is_draw)
   market=rr.get('market');origins=[s for s in rr.get('sources',[]) if s.get('source_id')=='domestic_market'];origin=origins[0] if len(origins)==1 else None
   reasons=quote_reasons(market,lab,origin);corroboration=None
   if isinstance(market,dict) and market.get('market_id'):
    if not rr.get('kickoff_at') or rr['kickoff_at'][11:16]!=str(g.get('G_TM')):reasons.append('official_kickoff_clock_conflict')
    if origin and origin.get('sha256')!=PINS['domestic_market_csv']['sha256']:reasons.append('raw_quote_origin_sha_conflict')
    reasons.extend(csv_quote_reasons(market_csv.get(market['market_id'],[]),market,day,home,away,lab,rr.get('kickoff_at')))
   if year==2024 and fid in rep:
    z=rep[fid];coverage['corroboration_fixture']+=1
    if (z['date'],z['home'],z['away'])!=(day,home,away) or (z['hg'],z['ag'])!=(lab['home_score'],lab['away_score']):reasons.append('corroboration_fixture_or_score_conflict')
    elif isinstance(market,dict) and isinstance(z.get('prices'),list) and len(z['prices'])==2 and z.get('order')==['home','away'] and all(valid_price(v) for v in z['prices']):
     coverage['corroboration_price_comparable']+=1
     if isinstance(market.get('selections'),dict) and all(valid_price(market['selections'].get(s)) for s in ('home','away')):
      if (float(market['selections']['home']),float(market['selections']['away']))!=(float(z['prices'][0]),float(z['prices'][1])):reasons.append('corroboration_price_conflict')
      else:coverage['corroboration_price_equal']+=1;corroboration={'source':'archived-reproduction','commit':COMMITS['archive'],'sha256':PINS['data/model_candidates/kbo/archived-reproduction.jsonl.gz']['sha256'],'same_fixture_and_score':True,'both_prices_equal':True,'used_for_substitution':False}
   if isinstance(market,dict) and market.get('market_id'):
    if market_freq[market['market_id']]>1:reasons.append('duplicate_market_id')
    if not re.fullmatch(r'proto:'+str(year)+r':\d+:\d+:full_game_2way',market['market_id']):reasons.append('market_id_format_or_season_conflict')
   if reasons:
    coverage['price_excluded_draws']+=int(is_draw);ex.append({'fixture_id':fid,'date':day,'reasons':sorted(set(reasons))});counts.update(set(reasons));continue
   winner='home' if lab['home_score']>lab['away_score'] else 'away' if lab['home_score']<lab['away_score'] else 'draw';coverage['price_eligible_draws']+=int(is_draw)
   result.append({'fixture_id':fid,'date':day,'home_id':home,'away_id':away,'winner':winner,'price_source':{'prices':{'home':market['selections']['home'],'away':market['selections']['away']},'origin':'archived_domestic_market_raw_quote','market_id':market['market_id'],'kind':market['kind'],'settlement_rule':market['settlement_rule'],'quote_tier':'retrospective_archived_raw_quote','odds_observed_at':market.get('odds_observed_at'),'historical_quote_receipt_proven':False,'execution_price_verified':False,'provenance':{'raw_row_source_commit':COMMITS['effects'],'raw_row_gzip_sha256':PINS[base+'rows.jsonl.gz']['sha256'],'market_source':origin,'source_csv_sha256_verified':True,'source_csv_quote_exactly_matched':True},'corroboration':corroboration},'result_evidence':{'official_game_id':g['G_ID'],'fixture_match_basis':basis,'official_schedule_sha256':se['sha256'],'official_schedule_received_at':se['received_at'],'historical_receipt_proven':False}})
  out[year]=result;labelout[year]=result_labels;excluded[year]=ex;summaries[year]={'target_fixtures':len(target[year]),'labels_verified':len(result_labels),'prices_eligible':len(result),'excluded_unique_fixtures':len(ex),'exclusion_reason_counts':dict(counts),'corroboration':dict(coverage),'source_market_present':sum(raw[x['fixture_id']].get('market') is not None for x in target[year]),'price_receipt_verified':0,'execution_verified':0}
 outputs={}
 for y in (2024,2025):
  outputs['prices-'+str(y)]=save('prices-'+str(y)+'.jsonl.gz',out[y]);outputs['labels-'+str(y)]=save('labels-'+str(y)+'.jsonl.gz',labelout[y]);outputs['exclusions-'+str(y)]=save('exclusions-'+str(y)+'.jsonl.gz',excluded[y])
 preexcluded=[{'fixture_id':k,'reasons':v['exclusion_reasons']} for k,v in sorted(rosters25.items()) if v['complete_verified'] is not True]
 if len(preexcluded)!=5:raise ValueError('preexisting_2025_roster_exclusion_mismatch')
 manifest={'schema':'kbo-retrospective-rawquote-label-2024-2025-v1','status':'ready_for_model_worker_frozen_inputs_only','contract_sha256':sha(contract),'source_pins':PINS,'original_price_builder':{'commit':builder_commit,'path':builder_path,'git_blob_sha256':sha(builder),'selection':'original builder excluded void rows and required archived final-score agreement'},'population':summaries,'preexisting_2025_roster_exclusions':preexcluded,'outputs_sha256':outputs,'excluded_fixture_reasons_may_overlap':True,'corroboration_policy':'2024 validation archive only; never replaces or adds a quote','quote_tier':'raw quote archived; original timestamp and execution unverified','official_results':'current official schedule was independently matched to archived scores, no historical receipt proof','source_2024_collection_limitation':{'request_spacing_violations_under_1_5_seconds':m24['request_spacing_violations_under_1_5_seconds'],'note':m24['request_spacing_note']},'draw_selection_limitation':'All 9 target draws in 2024 and all 22 target draws in 2025 are absent from original price rows; price-covered subsets contain zero draws. No prices were added from any other source.','no_models_scored':True,'no_new_data_collection':True,'pit_quote_receipt_verified':0,'execution_price_verified':0}
 tmp=HERE/'manifest.json.tmp';tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8',newline='\n');os.replace(tmp,HERE/'manifest.json');print(json.dumps({'population':summaries,'outputs_sha256':outputs},ensure_ascii=False))
if __name__=='__main__':main()
