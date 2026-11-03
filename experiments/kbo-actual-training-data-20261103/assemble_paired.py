"""Assemble paired research inputs only from finalized, Git-pinned source artifacts.

Pass each roster source via --source YEAR CHECKOUT COMMIT REPO_RELATIVE_FILE,
and the 2026 verification via --verification CHECKOUT COMMIT REPO_RELATIVE_FILE.
No labels are opened. The default adapter is the documented 2023 roster schema;
other-year adapters must be explicit and implemented after their final handoffs.
"""
import argparse,copy,gzip,hashlib,importlib.util,json,subprocess,sys
from collections import Counter
from datetime import date
from pathlib import Path
from build_rosters_2023 import SOURCE,FROZEN,fixed_file,gzwrite
from paired_core import training_pair,model_row,roles_projected,previous,valid_pitcher,value,verify_shared_histories
from history_index import build_old,build_fresh_pitch

HERE=Path(__file__).resolve().parent
KBO=Path(r'C:\Users\user\Documents\Codex\kbor')
EFFECTS=Path(r'C:\Users\user\Documents\Codex\kbeffects')
CONTRACT_SHA='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480'
def sha(b):return hashlib.sha256(b).hexdigest()
def pin(root,commit,rel,want=None,text_normalize=False):
 root=Path(root);path=root/rel;raw=path.read_bytes();blob=subprocess.check_output(['git','cat-file','blob',commit+':'+Path(rel).as_posix()],cwd=root)
 compared=raw.replace(b'\r\n',b'\n') if text_normalize else raw
 if compared!=blob or (want and sha(compared)!=want):raise ValueError('git_blob_mismatch:'+rel)
 return raw
def gz(raw):return [json.loads(x) for x in gzip.decompress(raw).splitlines()]
def index(rows):
 rows=list(rows)
 out={x['fixture_id']:x for x in rows}
 if len(out)!=len(rows):raise ValueError('duplicate_fixture')
 return out
def compare_selectors(counter,projected,actual):
 lineup_both=[];starter_both=[]
 for side in ('home','away'):
  p=projected[side];q=actual[side];line=p['hitters'];truth=q['hitters'];starter=p['starter']
  if line is None:counter['lineup_missing_sides']+=1;lineup_both.append(None)
  else:
   if len(line)!=9 or len(set(line))!=9:raise ValueError('invalid_projected_selector')
   counter['lineup_comparable_sides']+=1;counter['lineup_same_set_sides']+=int(set(line)==set(truth));counter['lineup_same_order_sides']+=int(line==truth);lineup_both.append(line==truth)
  if starter is None:counter['starter_missing_sides']+=1;starter_both.append(None)
  else:
   counter['starter_comparable_sides']+=1;match=starter==q['starter'];counter['starter_same_id_sides']+=int(match);starter_both.append(match)
 if None not in lineup_both:
  counter['lineup_comparable_games']+=1;counter['lineup_same_order_both_sides_games']+=int(all(lineup_both))
 if None not in starter_both:
  counter['starter_comparable_games']+=1;counter['starter_same_id_both_sides_games']+=int(all(starter_both))
def old_inputs():
 sys.path.insert(0,str(SOURCE/'experiments/kbo-player-data-20261103'));import build_prior_projection as prior
 games=gz(fixed_file(prior.GAMES,'c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce'));gmap={x['source_fixture_id']:x for x in games}
 if len(gmap)!=len(games):raise ValueError('game_duplicate')
 bat=json.loads(fixed_file(prior.BAT,'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'));pit=json.loads(fixed_file(prior.PIT,'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'))
 rows=index(map(json.loads,fixed_file(prior.ROWS,'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c').splitlines()))
 proj=index(gz(fixed_file(prior.OUT/'prior-projected-features.jsonl.gz','eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae')))
 if set(rows)!=set(proj) or set(rows)!=set(gmap):raise ValueError('original_fixture_cohort_mismatch')
 bh,ph=build_old(games,bat,pit,prior.innings_outs)
 return prior,games,gmap,rows,proj,bh,ph,pit

def adapter_module():
 rel='experiments/kbo-player-effects-20261103/original_adapter.py';path=EFFECTS/rel
 pin(EFFECTS,'d08ccb708033fb6b067e84e80eae81ea3c9b4cfa',rel,'581fac3ccd0d1aa0c82d9830776f8307b03aab89eab7d23206044a0417e9062f',True)
 spec=importlib.util.spec_from_file_location('frozen_paired_adapter',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def adapt_2023(root,commit,rel,roster_raw):
 directory=Path(rel).parent
 manifest_raw=pin(root,commit,(directory/'actual-rosters-2023-manifest.json').as_posix());manifest=json.loads(manifest_raw)
 if manifest.get('status')!='finalized_2023_source_only' or manifest.get('population')!=723 or manifest.get('historical_receipt_proven')!=0 or manifest.get('historical_publication_time_proven')!=0 or manifest.get('source_rows_commit')!=FROZEN:raise ValueError('2023_manifest_contract_mismatch')
 if sha(roster_raw)!=manifest['rows_sha256']:raise ValueError('2023_roster_manifest_sha_mismatch')
 pin(root,commit,(directory/'actual-rosters-2023-raw.jsonl.gz').as_posix(),manifest['raw_evidence_bundle_sha256'])
 handoff=pin(root,commit,(directory/'actual-rosters-2023-handoff.md').as_posix(),text_normalize=True)
 data=gz(roster_raw)
 if len(data)!=723 or sum(x.get('status')=='complete' for x in data)!=manifest['counts']['actual_complete']:raise ValueError('2023_manifest_eligibility_mismatch')
 return data,{'manifest_sha256':sha(manifest_raw),'handoff_sha256_lf':sha(handoff.replace(b'\r\n',b'\n')),'raw_bundle_sha256':manifest['raw_evidence_bundle_sha256']}

def adapt_2024(root,commit,rel,roster_raw):
 """Translate the finalized source-2024 contract without changing its evidence."""
 directory=Path(rel).parent
 def source(name,want=None):return pin(root,commit,(directory/name).as_posix(),want)
 manifest_raw=source('manifest.json');manifest=json.loads(manifest_raw)
 notes={}
 for name in ('README.md','verification.md'):
  relnote=(directory/name).as_posix();notes[name]=sha(pin(root,commit,relnote,text_normalize=True).replace(b'\r\n',b'\n'))
 if manifest.get('season')!=2024 or manifest.get('population')!=728 or manifest.get('contract_sha256')!=CONTRACT_SHA or manifest.get('historical_receipt_proven') is not False:raise ValueError('2024_manifest_contract_mismatch')
 if sha(roster_raw)!=manifest['rosters_sha256']:raise ValueError('2024_roster_manifest_sha_mismatch')
 source('official-evidence.tar.gz',manifest['evidence_bundle_sha256']);source('batter-identity-only.json',manifest['batter_identity_extract_sha256'])
 if manifest['input_sha256']!='66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c' or manifest['batter_source_sha256']!='24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8' or manifest['pitcher_identity_source_sha256']!='c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66':raise ValueError('2024_fixed_identity_source_mismatch')
 output=[];official_ids=set()
 for record in map(json.loads,roster_raw.splitlines()):
  if record.get('season')!=2024 or record.get('identity_tier')!='retrospective_only' or record.get('eligible_as_pregame_feature') is not False or record.get('historical_receipt_time') is not None or record.get('historical_publication_time') is not None:raise ValueError('2024_retrospective_status_invalid')
  reasons=record.get('exclusion_reasons') or [];complete=not reasons
  if complete:
   gid=record.get('official_game_id')
   if not gid or gid in official_ids:raise ValueError('2024_official_fixture_duplicate')
   official_ids.add(gid)
   if not str(record.get('lineup_semantics','')).startswith('confirmed_official_lineup_'):raise ValueError('2024_official_lineup_semantics')
  hitters={s:(record.get(s) or {}).get('ordered_hitters') for s in ('home','away')};starters={s:(record.get(s) or {}).get('starter') for s in ('home','away')}
  if complete and any(not starters[s] or starters[s].get('actual_record_verified') is not True or not hitters[s] for s in ('home','away')):raise ValueError('2024_actual_roster_incomplete')
  output.append({'fixture_id':record['fixture_id'],'season':2024,'date':record['date'],'teams':{'home':record['home_team'],'away':record['away_team']},'status':'complete' if complete else 'excluded','exclusions':reasons,'hitters':hitters,'starters':starters})
 if len(output)!=728 or sum(x['status']=='complete' for x in output)!=manifest['eligible']:raise ValueError('2024_manifest_eligibility_mismatch')
 return output,{'manifest_sha256':sha(manifest_raw),'handoff_and_verification_sha256_lf':notes,'raw_bundle_sha256':manifest['evidence_bundle_sha256'],'request_spacing_violations_under_1_5_seconds':manifest['request_spacing_violations_under_1_5_seconds'],'request_spacing_note':manifest['request_spacing_note']}

def adapt_2025(root,commit,rel,roster_raw):
 """Translate the finalized source-2025 contract without using target statistics."""
 directory=Path(rel).parent
 def source(name,want=None):return pin(root,commit,(directory/name).as_posix(),want)
 manifest_raw=source('manifest.json');manifest=json.loads(manifest_raw);handoff_raw=source('handoff.json');handoff=json.loads(handoff_raw)
 if manifest.get('status')!='ready_for_join' or handoff.get('status')!='ready_for_join' or manifest.get('denominator')!=736 or manifest.get('contract_sha256')!=CONTRACT_SHA or manifest.get('historical_receipt_proven') is not False or manifest.get('historical_publication_time_proven') is not False or manifest.get('target_game_statistics_used_as_features') is not False:raise ValueError('2025_manifest_contract_mismatch')
 if sha(manifest_raw)!=handoff['manifest_sha256'] or sha(roster_raw)!=manifest['lineups_gzip_sha256'] or sha(gzip.decompress(roster_raw))!=manifest['lineups_uncompressed_sha256']:raise ValueError('2025_roster_manifest_sha_mismatch')
 identity=source('identity-candidates-2025.jsonl.gz',manifest['identity_candidates_gzip_sha256'])
 if sha(gzip.decompress(identity))!=manifest['identity_candidates_uncompressed_sha256']:raise ValueError('2025_identity_candidates_uncompressed_sha_mismatch')
 source('raw-evidence.jsonl.gz',manifest['evidence_gzip_sha256']);source('pitcher-daily-identity.json',manifest['daily_identity_sha256']);source('unconfirmed-review.json',manifest['unconfirmed_review_sha256'])
 if manifest['source_rows_sha256']!='66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c' or manifest['identity_archive_sha256']!='24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8' or manifest['pitcher_identity_archive_sha256']!='c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66':raise ValueError('2025_fixed_identity_source_mismatch')
 output=[];official_ids=set()
 for record in gz(roster_raw):
  if record.get('season')!=2025 or record.get('eligible_as_pregame_feature') is not False or record.get('historical_receipt_proven') is not False or record.get('historical_publication_time_proven') is not False or record.get('actual_batting_starter_changes_verified') is not False:raise ValueError('2025_retrospective_status_invalid')
  reasons=record.get('exclusion_reasons') or [];complete=record.get('complete_verified') is True
  if complete!=(not reasons):raise ValueError('2025_complete_and_exclusion_mismatch')
  if complete:
   gid=record.get('official_game_id')
   if not gid or gid in official_ids or record.get('lineup_confirmed') is not True:raise ValueError('2025_official_fixture_duplicate_or_unconfirmed')
   official_ids.add(gid)
   if not str(record.get('batting_roster_semantics','')).startswith('official_confirmed_lineup_'):raise ValueError('2025_official_lineup_semantics')
  hitters={s:(record.get(s) or {}).get('ordered_hitters') for s in ('home','away')};starters={s:(record.get(s) or {}).get('starter') for s in ('home','away')}
  if complete and any(not starters[s] or starters[s].get('actual_record_verified') is not True or not hitters[s] for s in ('home','away')):raise ValueError('2025_actual_roster_incomplete')
  output.append({'fixture_id':record['fixture_id'],'season':2025,'date':record['date'],'teams':{s:(record.get(s) or {}).get('team',{}).get('name') for s in ('home','away')},'status':'complete' if complete else 'excluded','exclusions':reasons,'hitters':hitters,'starters':starters})
 if len(output)!=736 or sum(x['status']=='complete' for x in output)!=manifest['counts']['complete_verified']:raise ValueError('2025_manifest_eligibility_mismatch')
 return output,{'manifest_sha256':sha(manifest_raw),'handoff_sha256':sha(handoff_raw),'raw_bundle_sha256':manifest['evidence_gzip_sha256'],'daily_identity_sha256':manifest['daily_identity_sha256'],'unconfirmed_review_sha256':manifest['unconfirmed_review_sha256'],'new_requests':manifest['new_requests'],'source_kickoff_mismatches':manifest['counts']['source_kickoff_mismatches']}

def source_rosters(args,rows):
 sources={};meta={}
 for year,root,commit,rel in args.source:
  year=int(year)
  if year in sources or year not in (2023,2024,2025):raise ValueError('source_year_duplicate_or_invalid')
  data=pin(root,commit,rel);extra={}
  if year==2024:translated,extra=adapt_2024(root,commit,rel,data)
  elif year==2025:translated,extra=adapt_2025(root,commit,rel,data)
  else:translated,extra=adapt_2023(root,commit,rel,data)
  source=index(translated);expected={fid for fid,x in rows.items() if x['season']==year}
  if set(source)!=expected:raise ValueError('source_fixture_population_mismatch:'+str(year))
  for fid,r in source.items():
   if r.get('season')!=year or r.get('status') not in ('complete','excluded') or 'exclusions' not in r:raise ValueError('unrecognized_source_schema:'+str(year))
   if (r['status']=='excluded' and not r['exclusions']) or (r['status']=='complete' and r['exclusions']):raise ValueError('source_exclusion_reason_mismatch:'+str(year))
  sources[year]=source;meta[str(year)]={'commit':commit,'path':rel,'sha256':sha(data),'population':len(source),'statuses':dict(Counter(r['status'] for r in source.values())),**extra}
 if set(sources)!={2023,2024,2025}:raise ValueError('three_final_source_commits_required')
 return sources,meta

def fresh_pitch(prior,games,pit):
 root=KBO/'experiments/kbo-official-record-gap-20261103';pins={'artifacts/normalized-records.jsonl.gz':'e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334','v2/artifacts/supplement-v2.jsonl.gz':'0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84'};data=[]
 for rel,want in pins.items():
  path=root/rel;raw=pin(KBO,'d11367b7b37f3ea34f4c08987ca042af9235575e',path.relative_to(KBO).as_posix(),want);data.append(index(gz(raw)))
 return build_fresh_pitch(games,pit,prior.innings_outs,*data)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',nargs=4,action='append',required=True,metavar=('YEAR','CHECKOUT','COMMIT','FILE'));ap.add_argument('--verification',nargs=3,required=True,metavar=('CHECKOUT','COMMIT','FILE'));args=ap.parse_args()
 if sha((HERE/'parent-contract.json').read_bytes())!=CONTRACT_SHA:raise ValueError('parent_contract_mismatch')
 adapter=adapter_module();prior,games,gmap,rows,baseline,bh,ph,pit=old_inputs();sources,meta=source_rosters(args,rows)
 projected=[];actual=[];excluded=Counter();excluded_fixtures=[];selector={str(y):Counter() for y in (2023,2024,2025,2026)};dates={fid:date.fromisoformat(g['date']) for fid,g in gmap.items()}
 for fid,row in sorted(rows.items(),key=lambda z:(gmap[z[0]]['date'],z[0])):
  year=row['season']
  if year not in sources:continue
  roster=sources[year][fid]
  if roster['status']!='complete':
   excluded_fixtures.append({'fixture_id':fid,'season':year,'reasons':roster['exclusions']})
   for reason in roster['exclusions']:excluded[str(year)+':'+reason]+=1
   continue
  day=gmap[fid]['date'];raw={'fixture_id':fid,'event_date':day,'season':year,'home_id':row['home_id'],'away_id':row['away_id']}
  if baseline[fid].get('scenario')!='projected_from_prior_games' or baseline[fid].get('availability_tier')!='retrospective_unproven':raise ValueError('training_scenario_or_availability_mismatch')
  adapter.check_nested(baseline[fid]['features'],fid,dates[fid],dates);adapter.check_nested(baseline[fid]['projection'],fid,dates[fid],dates)
  p,a=training_pair(baseline[fid],raw,roster,bh,ph,adapter);compare_selectors(selector[str(year)],p['roster'],a['roster']);projected.append(p);actual.append(a)
 if [x['fixture_id'] for x in projected]!=[x['fixture_id'] for x in actual]:raise ValueError('training_pair_order')
 verroot,vercommit,verrel=args.verification;verraw=pin(verroot,vercommit,verrel);verification=index(gz(verraw))
 verdir=Path(verrel).parent;vmraw=pin(verroot,vercommit,(verdir/'actual-starters-2026-manifest.json').as_posix());vm=json.loads(vmraw)
 if vm.get('status')!='finalized_2026_starter_verification' or vm.get('population')!=709 or vm.get('historical_receipt_proven')!=0 or sha(verraw)!=vm['sha256']['records']:raise ValueError('2026_verification_manifest_mismatch')
 pin(verroot,vercommit,(verdir/'actual-starters-2026-raw.jsonl.gz').as_posix(),vm['sha256']['raw'])
 groups=json.loads(fixed_file(prior.OUT/'official-full-2026-cohorts.jsonl'));ids=groups['all']
 if len(ids)!=709 or set(verification)!=set(ids):raise ValueError('verification_population_mismatch')
 tests={name:index(gz(fixed_file(prior.OUT/('fresh-history-2026-'+name+'.jsonl.gz')))) for name in ('projected','official_batting_and_starter')}
 if any(set(v)!=set(ids) for v in tests.values()):raise ValueError('2026_input_population_mismatch')
 fresh=fresh_pitch(prior,games,pit);testp=[];testa=[];actual_change=Counter()
 for fid in ids:
  vr=verification[fid]
  if vr.get('status')!='complete':
   excluded_fixtures.append({'fixture_id':fid,'season':2026,'reasons':vr.get('exclusions') or ['verification_missing_reason']})
   for reason in vr.get('exclusions') or ['verification_missing_reason']:excluded['2026:'+reason]+=1
   continue
  b=tests['projected'][fid];a=copy.deepcopy(tests['official_batting_and_starter'][fid]);day=gmap[fid]['date'];row=rows[fid]
  if b.get('scenario')!='fresh_history_projected' or a.get('scenario')!='fresh_history_official_batting_and_starter':raise ValueError('2026_scenario_mismatch')
  for context in ('official_lineup_context','official_schedule_pitcher_context'):
   if any(a[context].get(k) is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('unproven_historical_availability_promoted')
  if vr['date'] not in (day,day.replace('-','')):raise ValueError('2026_verification_date_mismatch')
  if vr['official_game_id']!=a['official_lineup_context']['official_game_id']:raise ValueError('2026_verification_game_mismatch')
  if any(str(vr['teams'][s])!=str(a['official_lineup_context'][s+'_team_id']) for s in ('home','away')):raise ValueError('2026_verification_team_mismatch')
  proles=roles_projected(b);aroles={}
  for side in ('home','away'):
   p=vr['starters'][side];line=a['official_lineup_context']['lineups'][side];hitters=[x['player_id'] for x in line]
   if not p['actual_record_verified'] or len(hitters)!=9 or len(set(hitters))!=9:raise ValueError('2026_actual_roster_invalid')
   if [x['order'] for x in line]!=list(range(1,10)) or [x['player_id'] for x in a['features'][side+'_lineup_strength']['value']]!=hitters:raise ValueError('2026_actual_lineup_feature_mismatch')
   expected=a['official_schedule_pitcher_context']['pitchers'][side]['player_id'];pid=p['player_id'];history=previous(fresh.get(pid,[]),day,valid_pitcher)
   actual_change['schedule_diff_sides']+=int(pid!=expected)
   old=a['features'][side+'_starter_strength']['value']
   if pid==expected and (old or {}).get('appearances',[])!=history:raise ValueError('2026_fresh_history_mismatch')
   a['features'][side+'_starter_strength']=value({'projected_player_id':pid,'appearances':copy.deepcopy(history)} if history else None,history)
   aroles[side]={'hitters':hitters,'starter':pid}
  for key in b['features']:
   if not key.endswith(('lineup_strength','starter_strength')) and b['features'][key]!=a['features'][key]:raise ValueError('2026_nonplayer_mismatch')
  verify_shared_histories(b,a);compare_selectors(selector['2026'],proles,aroles)
  raw={'fixture_id':fid,'event_date':day,'season':2026,'home_id':row['home_id'],'away_id':row['away_id']}
  adapter.check_nested(a['features'],fid,dates[fid],dates);adapter.check_nested(b['features'],fid,dates[fid],dates)
  testp.append(model_row(b,raw,proles,adapter));testa.append(model_row(a,raw,aroles,adapter))
 if [x['fixture_id'] for x in testp]!=[x['fixture_id'] for x in testa]:raise ValueError('test_pair_order')
 outputs={}
 for name,items in [('train-projected',projected),('train-actual',actual),('test-projected',testp),('test-actual',testa)]:outputs[name]=gzwrite(HERE/('paired-'+name+'.jsonl.gz'),items)
 info={'status':'finalized_paired_research_inputs','parent_contract_sha256':CONTRACT_SHA,'old_data_commit':FROZEN,'source_rosters':meta,'verification':{'commit':vercommit,'path':verrel,'sha256':sha(verraw),'manifest_sha256':sha(vmraw),'raw_bundle_sha256':vm['sha256']['raw']},'training_by_year':dict(Counter(str(x['season']) for x in projected)),'test_original_population':709,'test_verified_actual':len(testp),'exclusions':dict(excluded),'excluded_fixture_count':len(excluded_fixtures),'excluded_fixtures':excluded_fixtures,'exclusion_reason_counts_may_overlap':True,'selector_match_counts_by_year':{k:dict(v) for k,v in selector.items()},'shared_selected_player_histories_canonical_bytes_equal':True,'history_population_policy':'All validated prior component records remain available, including records from fixtures excluded as paired training rows.','actual_starter_schedule_differences':dict(actual_change),'output_sha256':outputs,'no_labels_read':True,'target_same_day_future_features_excluded':True,'point_in_time_proven':0,'no_model_fit_or_score':True}
 (HERE/'paired-inputs-manifest.json').write_text(json.dumps(info,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps(info,ensure_ascii=False))
if __name__=='__main__':main()
