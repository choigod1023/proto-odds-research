"""Audit frozen four paired files against pinned roster sources and history boundaries."""
import collections,gzip,hashlib,json,subprocess,sys
from pathlib import Path
import reference as ref
from audit_paired_2023_2026 import assert_prior_tree,pin
D=Path(r'C:\Users\user\Documents\Codex\kbtdata');A24=Path(r'C:\Users\user\Documents\Codex\kbt24');A25=Path(r'C:\Users\user\Documents\Codex\kbt25');BASE=Path(r'C:\Users\user\Documents\Codex\kbodata');LINE=Path(r'C:\Users\user\Documents\Codex\klu')
C='58556de08c64604276de8682373d3ccc8d535d7c';B='13291d23e855e0d500327cb39ac64a480e395837';L='b24739cf52435070586f8687011fd473a16123e4';P='experiments/kbo-actual-training-data-20261103/'
S24='e8ba21f970f73594848e75cc6515a1dd3a994bf1';S25='11aa00812d45fdcf35e462413acfc48935dd530d'
def git(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def gz(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def idx(rows):
 d={x['fixture_id']:x for x in rows}
 if len(d)!=len(rows):raise ValueError('duplicate fixture')
 return d
def need(x,msg):
 if not x:raise ValueError(msg)
def valid_bat(x):return all(type(x.get(k)) is int and x[k]>=0 for k in ('ab','hit','bb')) and x['hit']<=x['ab']
def valid_pitch(x):return all(type(x.get(k)) is int and x[k]>=0 for k in ('er','outs'))
def canon(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def main():
 manifest=json.loads(git(D,C,P+'paired-inputs-manifest.json'));expected_hash={'train-actual':'0d760f7cc1f993792d8ab5e697a9e77c903e5abc4f90823730baabcdc1c4adf9','train-projected':'300da51d893770af62317ef975efec8e37edd5a60445c3c545daadf3e7a39eb8','test-actual':'f904267d65e790fbde663b2280f0489b6d8a9df26a73f5e9c9ca713bc0a64a5d','test-projected':'2ef8e3a7f17b9f43262b875d3b913f0420e23f31a25aaba8f943131445a35bc6'}
 files={}
 for k,h in expected_hash.items():
  raw=git(D,C,P+'paired-'+k+'.jsonl.gz');need(sha(raw)==h==manifest['output_sha256'][k],'paired blob hash '+k);files[k]=gz(raw)
 for name in ('assemble_paired.py','paired_core.py','history_index.py','build_rosters_2023.py','collect_lineups.py','collect_actual_starters.py'):pin(D,C,P+name)
 pin(BASE,B,'experiments/kbo-player-data-20261103/build_prior_projection.py')
 sys.path.insert(0,str(D/P));import assemble_paired as a
 from history_index import build_fresh_bat
 adapter=a.adapter_module();prior,games,gmap,allrows,oldbase,bh,ph,pit=a.old_inputs()
 # Construct official source indexes directly from the previously audited Git blobs.
 sources={2023:idx(gz(git(D,'c4e6bdb67784ceaf76e39707e1b8083f222c0a7d',P+'actual-rosters-2023.jsonl.gz'))),2024:idx(list(map(json.loads,git(A24,S24,'experiments/kbo-actual-2024-20261103/rosters.jsonl').splitlines()))),2025:idx(gz(git(A25,S25,'experiments/kbo-actual-lineups-2025-20261103/actual-lineups-2025.jsonl.gz')))}
 ver=idx(gz(git(D,'c4e6bdb67784ceaf76e39707e1b8083f222c0a7d',P+'actual-starters-2026.jsonl.gz')));official=idx(list(map(json.loads,git(LINE,L,'experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl').splitlines())))
 cutoff=manifest['excluded_fixtures'];excluded={x['fixture_id'] for x in cutoff};need(len(excluded)==5 and all(not sources[2025][x]['complete_verified'] for x in excluded),'exclusion mismatch')
 expected_train=set(sources[2023])|set(sources[2024])|({x for x,z in sources[2025].items() if z['complete_verified']});need(len(expected_train)==2182 and set(sources[2025])-expected_train==excluded,'training cohort')
 groups=json.loads(a.fixed_file(prior.OUT/'official-full-2026-cohorts.jsonl'));expected_test=set(groups['all']);need(len(expected_test)==709 and set(ver)==expected_test,'test cohort')
 freshbase=idx(gz(a.fixed_file(prior.OUT/'fresh-history-2026-projected.jsonl.gz')))
 gaproot=a.KBO/'experiments/kbo-official-record-gap-20261103';gapcommit='d11367b7b37f3ea34f4c08987ca042af9235575e'
 def gap(rel,h):return idx(gz(a.pin(a.KBO,gapcommit,(gaproot/rel).relative_to(a.KBO).as_posix(),h)))
 v1=gap('artifacts/normalized-records.jsonl.gz','e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334');v2=gap('v2/artifacts/supplement-v2.jsonl.gz','0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84')
 freshb=build_fresh_bat(games,json.loads(a.fixed_file(prior.BAT)),v1,v2);freshp=a.fresh_pitch(prior,games,pit);counts=collections.Counter();yearcounts=collections.Counter();shared=collections.Counter()
 for part in ('train','test'):
  ps=files[part+'-projected'];actual=files[part+'-actual'];need([x['fixture_id'] for x in ps]==[x['fixture_id'] for x in actual] and len(idx(ps))==len(ps),'matched pair IDs')
  need(set(idx(ps))==(expected_train if part=='train' else expected_test),'paired fixture population')
  for p,q in zip(ps,actual):
   fid=p['fixture_id'];day=gmap[fid]['date'];src=allrows[fid];year=src['season'];baseline=oldbase[fid] if part=='train' else freshbase[fid];
   need(p['raw_features']==baseline['features'] and p['event_date']==q['event_date']==day and p['season']==q['season']==year and p['home_id']==q['home_id']==src['home_id'] and p['away_id']==q['away_id']==src['away_id'],'identity or projected baseline '+fid)
   for row in (p,q):
    need(row['historical_receipt_proven'] is False and row['availability_tier']=='retrospective_unproven','receipt promotion '+fid);ref.assert_roster_feature_identity(row,row['raw_features']);assert_prior_tree(row['raw_features'],fid,day)
    need(set(row['features'])==set(row['raw_features']),'feature set '+fid)
    for key,entry in row['raw_features'].items():
     need(entry['availability_basis']=='prior_calendar_day_assumed_completed_not_received' and entry['completed_at'] is None and entry['received_at'] is None,'availability '+fid)
     for sourceid in entry.get('source_fixture_ids',[]):need(sourceid in gmap and sourceid!=fid and gmap[sourceid]['date']<day,'temporal source '+fid)
     got=row['features'][key]['value'];calc=ref.scalars(key,entry['value']);need(got==calc or got is not None and calc is not None and abs(got-calc)<1e-12,'independent scalar '+fid+' '+key)
   for key in p['raw_features']:
    if not key.endswith(('lineup_strength','starter_strength')):need(p['raw_features'][key]==q['raw_features'][key] and p['features'][key]==q['features'][key],'nonplayer or bullpen mismatch '+fid)
   if part=='train':
    roster=sources[year][fid]
    if year==2023:need(roster['status']=='complete','ineligible 2023 '+fid)
    elif year==2024:need(not roster['exclusion_reasons'],'ineligible 2024 '+fid)
    else:need(roster['complete_verified'],'ineligible 2025 '+fid)
   for side in ('home','away'):
    if part=='test':
     lineup=official[fid]['lineups'][side];actual_ids=[x['player_id'] for x in lineup];starter=ver[fid]['starters'][side]['player_id'];need([x['order'] for x in lineup]==list(range(1,10)),'test lineup order '+fid)
    elif year==2023:actual_ids=[x['player_id'] for x in roster['hitters'][side]];starter=roster['starters'][side]['player_id']
    else:actual_ids=[x['player_id'] for x in roster[side]['ordered_hitters']];starter=roster[side]['starter']['player_id']
    need(q['roster'][side]=={'hitters':actual_ids,'starter':starter},'official actual identity '+fid)
    expectedproj={'hitters':baseline['projection'][side+'_lineup']['player_ids'],'starter':baseline['projection'][side+'_starter']['player_id']};need(p['roster'][side]==expectedproj,'projected identity '+fid)
    before=p['raw_features'][side+'_lineup_strength']['value'] or [];after=q['raw_features'][side+'_lineup_strength']['value'] or [];pd={x['player_id']:x['history'] for x in before};ad={x['player_id']:x['history'] for x in after}
    for pid in set(pd)&set(ad):need(canon(pd[pid])==canon(ad[pid]),'shared batter history '+fid);shared['batter']+=1
    for row,idxb,idxp in ((p,freshb if part=='test' else bh,freshp if part=='test' else ph),(q,freshb if part=='test' else bh,freshp if part=='test' else ph)):
     for player in row['raw_features'][side+'_lineup_strength']['value'] or []:
      h=[z for z in idxb.get(player['player_id'],[]) if z['date']<day and valid_bat(z)][-10:];need((player['history'] or [])==h,'canonical batter history '+fid);counts['excluded_history_mentions']+=sum(z['fixture_id'] in excluded for z in h)
     pid=row['roster'][side]['starter'];h=[z for z in idxp.get(pid,[]) if z['date']<day and valid_pitch(z)][-10:] if pid else [];item=row['raw_features'][side+'_starter_strength']['value'];need((not h and item is None) or (bool(h) and item is not None and item['projected_player_id']==pid and item['appearances']==h),'canonical pitcher history '+fid);counts['excluded_history_mentions']+=sum(z['fixture_id'] in excluded for z in h)
    if p['roster'][side]['starter']==q['roster'][side]['starter'] and p['roster'][side]['starter'] is not None:
     need(canon(p['raw_features'][side+'_starter_strength']['value'])==canon(q['raw_features'][side+'_starter_strength']['value']),'shared pitcher history '+fid);shared['pitcher']+=1
   counts[part+'_pairs']+=1;yearcounts[str(year)]+=1
 need(dict(yearcounts)=={'2023':723,'2024':728,'2025':731,'2026':709},'year denominator');need(not excluded&set(idx(files['train-actual'])),'excluded in train')
 special=next(x for x in files['train-actual'] if x['fixture_id']=='77771023SSHT02024');need(special['event_date']=='2024-10-23','resumption date');assert_prior_tree(special['raw_features'],special['fixture_id'],special['event_date'])
 print(json.dumps({'commit':C,'counts':counts,'years':yearcounts,'shared_histories':shared,'excluded_fixtures':sorted(excluded),'resumed_2024_target_date':special['event_date'],'historical_receipt_proven':False,'calculated_model_performance':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
