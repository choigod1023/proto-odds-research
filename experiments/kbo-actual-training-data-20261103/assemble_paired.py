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
from paired_core import training_pair,model_row,roles_projected,previous,valid_pitcher,value
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

def source_rosters(args,rows):
 sources={};meta={}
 for year,root,commit,rel in args.source:
  year=int(year)
  if year in sources or year not in (2023,2024,2025):raise ValueError('source_year_duplicate_or_invalid')
  data=pin(root,commit,rel);source=index(gz(data));expected={fid for fid,x in rows.items() if x['season']==year}
  if set(source)!=expected:raise ValueError('source_fixture_population_mismatch:'+str(year))
  for fid,r in source.items():
   if r.get('season')!=year or r.get('status') not in ('complete','excluded') or 'exclusions' not in r:raise ValueError('unrecognized_source_schema:'+str(year))
  sources[year]=source;meta[str(year)]={'commit':commit,'path':rel,'sha256':sha(data),'population':len(source)}
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
 projected=[];actual=[];excluded=Counter();dates={fid:date.fromisoformat(g['date']) for fid,g in gmap.items()}
 for fid,row in sorted(rows.items(),key=lambda z:(gmap[z[0]]['date'],z[0])):
  year=row['season']
  if year not in sources:continue
  roster=sources[year][fid]
  if roster['status']!='complete':
   for reason in roster['exclusions']:excluded[str(year)+':'+reason]+=1
   continue
  day=gmap[fid]['date'];raw={'fixture_id':fid,'event_date':day,'season':year,'home_id':row['home_id'],'away_id':row['away_id']}
  adapter.check_nested(baseline[fid]['features'],fid,dates[fid],dates);adapter.check_nested(baseline[fid]['projection'],fid,dates[fid],dates)
  p,a=training_pair(baseline[fid],raw,roster,bh,ph,adapter);projected.append(p);actual.append(a)
 if [x['fixture_id'] for x in projected]!=[x['fixture_id'] for x in actual]:raise ValueError('training_pair_order')
 verroot,vercommit,verrel=args.verification;verraw=pin(verroot,vercommit,verrel);verification=index(gz(verraw))
 groups=json.loads(fixed_file(prior.OUT/'official-full-2026-cohorts.jsonl'));ids=groups['all']
 if len(ids)!=709 or set(verification)!=set(ids):raise ValueError('verification_population_mismatch')
 tests={name:index(gz(fixed_file(prior.OUT/('fresh-history-2026-'+name+'.jsonl.gz')))) for name in ('projected','official_batting_and_starter')}
 if any(set(v)!=set(ids) for v in tests.values()):raise ValueError('2026_input_population_mismatch')
 fresh=fresh_pitch(prior,games,pit);testp=[];testa=[];actual_change=Counter()
 for fid in ids:
  vr=verification[fid]
  if vr.get('status')!='complete':
   for reason in vr.get('exclusions') or ['verification_missing_reason']:excluded['2026:'+reason]+=1
   continue
  b=tests['projected'][fid];a=copy.deepcopy(tests['official_batting_and_starter'][fid]);day=gmap[fid]['date'];row=rows[fid]
  if vr['date'] not in (day,day.replace('-','')):raise ValueError('2026_verification_date_mismatch')
  if vr['official_game_id']!=a['official_lineup_context']['official_game_id']:raise ValueError('2026_verification_game_mismatch')
  proles=roles_projected(b);aroles={}
  for side in ('home','away'):
   p=vr['starters'][side];line=a['official_lineup_context']['lineups'][side];hitters=[x['player_id'] for x in line]
   if not p['actual_record_verified'] or len(hitters)!=9 or len(set(hitters))!=9:raise ValueError('2026_actual_roster_invalid')
   expected=a['official_schedule_pitcher_context']['pitchers'][side]['player_id'];pid=p['player_id'];history=previous(fresh.get(pid,[]),day,valid_pitcher)
   actual_change['schedule_diff_sides']+=int(pid!=expected)
   old=a['features'][side+'_starter_strength']['value']
   if pid==expected and (old or {}).get('appearances',[])!=history:raise ValueError('2026_fresh_history_mismatch')
   a['features'][side+'_starter_strength']=value({'projected_player_id':pid,'appearances':copy.deepcopy(history)} if history else None,history)
   aroles[side]={'hitters':hitters,'starter':pid}
  for key in b['features']:
   if not key.endswith(('lineup_strength','starter_strength')) and b['features'][key]!=a['features'][key]:raise ValueError('2026_nonplayer_mismatch')
  raw={'fixture_id':fid,'event_date':day,'season':2026,'home_id':row['home_id'],'away_id':row['away_id']}
  adapter.check_nested(a['features'],fid,dates[fid],dates);adapter.check_nested(b['features'],fid,dates[fid],dates)
  testp.append(model_row(b,raw,proles,adapter));testa.append(model_row(a,raw,aroles,adapter))
 if [x['fixture_id'] for x in testp]!=[x['fixture_id'] for x in testa]:raise ValueError('test_pair_order')
 outputs={}
 for name,items in [('train-projected',projected),('train-actual',actual),('test-projected',testp),('test-actual',testa)]:outputs[name]=gzwrite(HERE/('paired-'+name+'.jsonl.gz'),items)
 info={'status':'finalized_paired_research_inputs','parent_contract_sha256':CONTRACT_SHA,'old_data_commit':FROZEN,'source_rosters':meta,'verification':{'commit':vercommit,'path':verrel,'sha256':sha(verraw)},'training_by_year':dict(Counter(str(x['season']) for x in projected)),'test_original_population':709,'test_verified_actual':len(testp),'exclusions':dict(excluded),'actual_starter_schedule_differences':dict(actual_change),'output_sha256':outputs,'no_labels_read':True,'target_same_day_future_features_excluded':True,'point_in_time_proven':0,'no_model_fit_or_score':True}
 (HERE/'paired-inputs-manifest.json').write_text(json.dumps(info,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps(info,ensure_ascii=False))
if __name__=='__main__':main()
