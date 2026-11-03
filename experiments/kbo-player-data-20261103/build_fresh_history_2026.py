"""Build retrospective, strictly prior-calendar-day 709-game inputs; no scoring."""
import copy, gzip, hashlib, json, os, statistics, subprocess, sys
from collections import Counter, defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
K=Path(r'C:\Users\user\Documents\Codex\kbor')
SRC=K/'experiments/kbo-official-record-gap-20261103'
COMMIT='d11367b7b37f3ea34f4c08987ca042af9235575e'
full=__import__('build_official_full_2026')
prior=__import__('build_prior_projection')
h1,h2=full.h1,full.h2
FIXED={
 'artifacts/normalized-records.jsonl.gz':'e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334',
 'v2/artifacts/supplement-v2.jsonl.gz':'0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84',
 'artifacts/raw-responses.jsonl.gz':'8aeaf579f7ac84a642c3017810f8cf42c101bda2f6290a7fa3779a1d6fa72500',
 'v2/artifacts/official-daily-raw.jsonl.gz':'53ed7b68ee3c8c2f642f114bff7138d28d80c01932b1841911e19032a543ac39'}
CUT='2026-07-26'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def readgz(p):
 with gzip.open(p,'rt',encoding='utf8') as f: return list(map(json.loads,f))
def index(rows,key='fixture_id'):
 out={}
 for x in rows:
  k=x[key]
  if k in out: raise ValueError('duplicate '+key+': '+str(k))
  out[k]=x
 return out
def integer(x): return type(x) is int and x>=0
def ev(e):
 if not isinstance(e,dict) or e.get('http_status')!=200 or not e.get('url') or len(e.get('sha256',''))!=64: raise ValueError('invalid source evidence')
 if datetime.fromisoformat(e['received_at']).utcoffset() is None: raise ValueError('receipt time must include timezone')
 return {k:e[k] for k in ('url','sha256','received_at')}
def save(name,rows):
 p=HERE/name; tmp=p.with_name(p.name+'.tmp')
 with tmp.open('wb') as f:
  with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
   for x in rows:z.write((json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8'))
 os.replace(tmp,p);return sha(p)
def stat(gaps,missing):
 g=sorted(gaps)
 return {'observed':len(g),'missing':missing,'median_days':statistics.median(g) if g else None,'p90_days_nearest_rank':g[(9*len(g)+9)//10-1] if g else None,'max_days':max(g) if g else None,**{'over_'+str(n)+'_days':sum(x>n for x in g) for n in (14,30,60,90)}}

def main():
 # Exact working bytes and commit blobs, plus independent audits against raw response bundles.
 checks=full.verify_frozen_inputs()
 for name,want in FIXED.items():
  p=SRC/name; blob=subprocess.check_output(['git','cat-file','blob',COMMIT+':'+p.relative_to(K).as_posix()],cwd=K)
  if sha(p)!=want or p.read_bytes()!=blob:raise ValueError('source bytes mismatch: '+name)
  checks['gap/'+name]={'sha256':want,'exact_git_blob_equal':True}
 for args in [ ['scripts/audit_kbo_official_record_gap.py',str(SRC/'artifacts/normalized-records.jsonl.gz'),str(SRC/'artifacts/raw-responses.jsonl.gz')], ['scripts/audit_kbo_daily_supplement.py','--v1',str(SRC/'artifacts/normalized-records.jsonl.gz'),'--v1-raw',str(SRC/'artifacts/raw-responses.jsonl.gz'),'--supplement',str(SRC/'v2/artifacts/supplement-v2.jsonl.gz'),'--daily-raw',str(SRC/'v2/artifacts/official-daily-raw.jsonl.gz')]]:
  subprocess.run([sys.executable,*args],cwd=K,check=True,stdout=subprocess.DEVNULL)
 full.h1.CAPTURE=full.CAPTURE;full.h1.source_bytes.cache_clear();full.h1.profile_name=lru_cache(maxsize=8192)(full.h1.profile_name)
 groups=json.loads((HERE/'official-full-2026-cohorts.jsonl').read_text(encoding='utf8'))
 if (len(groups['all']),len(groups['original_193']),len(groups['new_complement']))!=(709,193,516) or set(groups['original_193'])|set(groups['new_complement'])!=set(groups['all']) or set(groups['original_193'])&set(groups['new_complement']):raise ValueError('cohort mismatch')
 rows=index(map(json.loads,(HERE/'rows.jsonl').read_text(encoding='utf8').splitlines()))
 bases=index(readgz(HERE/'prior-projected-features.jsonl.gz'))
 games=readgz(prior.GAMES); game_map=index(games,'source_fixture_id')
 if any(f not in game_map or f not in bases or f not in rows for f in groups['all']):raise ValueError('missing cohort fixture')
 official=index(map(json.loads,full.SOURCE.read_text(encoding='utf8').splitlines()))
 if len(official)!=726 or len({v['official_game_id'] for v in official.values()})!=726:raise ValueError('official game identity duplicate')
 selections={}
 for fid,record in official.items():
  lineup,pitch,reason=full.inspect_official(record,rows[fid])
  if reason is None:selections[fid]=(lineup,pitch)
  elif reason=='lineup_player_ids_unavailable':
   schedule=json.loads(h1.check_evidence(record['schedule_evidence']));pitch,err=h2.schedule_pitchers(record,schedule)
   selections[fid]=(None,pitch if not err else None)
  else:raise ValueError('unexpected official exclusion '+str(reason))
 if set(groups['all'])!={fid for fid,(l,p) in selections.items() if l and p}:raise ValueError('official validated cohort mismatch')
 v1=index(readgz(SRC/'artifacts/normalized-records.jsonl.gz')); v2=index(readgz(SRC/'v2/artifacts/supplement-v2.jsonl.gz'))
 if len(v1)!=256 or set(v1)!=set(v2):raise ValueError('supplement cohort mismatch')
 for fid,r in v1.items():
  s=v2[fid]; o=official[fid]; g=game_map[fid]
  if r['status']!='ok' or not r['completed'] or g['date']<=CUT or r['game_date']!=g['date'].replace('-',''):raise ValueError('gap game date/status mismatch')
  for k in ('official_game_id','game_date','series_id'):
   if r[k]!=s[k] or str(r[k])!=str(o[k]):raise ValueError('supplement crosswalk mismatch '+k)
  if str(r['header_no'])!=str(o['header_no']) or r['team_ids']!={'home':o['home_team_id'],'away':o['away_team_id']} or s['source_v1_gzip_sha256']!=FIXED['artifacts/normalized-records.jsonl.gz']:raise ValueError('game/team/header mismatch')
  ev(r['evidence']['box'])
 batraw=json.loads(prior.BAT.read_text(encoding='utf8'));pitraw=json.loads(prior.PIT.read_text(encoding='utf8')); starter_groups=defaultdict(list)
 for item in json.loads(prior.ST.read_text(encoding='utf8')):starter_groups[item['gameId']].append(item)
 starters={k:v[0] for k,v in starter_groups.items() if len(v)==1}
 byday=defaultdict(list)
 for g in games:byday[g['date']].append(g)
 teams=defaultdict(list);bh=defaultdict(list);ph=defaultdict(list);out={k:[] for k in ('projected','official_batting_only','official_starter_only','official_batting_and_starter')}
 incomplete=[];counts=Counter(); cohort=set(groups['all']); dates={f:game_map[f]['date'] for f in cohort}
 for day,fixtures in sorted(byday.items()):
  # Build all targets before adding ANY record from the same date.
  for g in sorted(fixtures,key=lambda x:x['source_fixture_id']):
   fid=g['source_fixture_id']
   if fid not in cohort:continue
   projected=prior.scenario(g,teams,bh,ph); base=copy.deepcopy(bases[fid]);base['projection']=projected['projection']
   for side in ('home','away'):
    pick=base['projection'][side+'_lineup'];source_ids=pick['source_fixture_ids']
    if source_ids and source_ids[0] in official:
     pick['source_evidence']={'lineup':ev(official[source_ids[0]]['lineup_evidence']),'schedule':ev(official[source_ids[0]]['schedule_evidence'])}
    for candidate in base['projection'][side+'_starter']['rotation_candidates']:
     if candidate['fixture_id'] in official:candidate['source_evidence']={'schedule':ev(official[candidate['fixture_id']]['schedule_evidence'])}
   for side in ('home','away'):
    for component in ('lineup_strength','starter_strength','bullpen_load'):base['features'][side+'_'+component]=projected['features'][side+'_'+component]
   base['scenario']='fresh_history_projected';base['warning']='Retrospective assumption only; historical publication, completion and receipt are not established.'
   selected,pitch=selections[fid];o=official[fid]
   bat=h1.replace_lineups(base,selected,bh,day,o)
   pf=h2.pitcher_features(base,pitch,ph,day)
   starter=h2.apply(base,pf,pitch,o,'official_starter_only');both=h2.apply(bat,pf,pitch,o,'official_batting_and_starter')
   for key,row in [('projected',base),('official_batting_only',bat),('official_starter_only',starter),('official_batting_and_starter',both)]:
    row['scenario']='fresh_history_'+key
    for side in ('home','away'):
     for feat in ('lineup_strength','starter_strength','bullpen_load'):
      v=row['features'][side+'_'+feat]
      if any(datesrc>=day for datesrc in [game_map[x]['date'] for x in v['source_fixture_ids']]):raise ValueError('target-day or future record')
    out[key].append(row)
  # Prior raw detail stops at July 26; later inputs only from the validated gap source.
  for g in sorted(fixtures,key=lambda x:x['source_fixture_id']):
   fid=g['source_fixture_id']; b=batraw.get(fid) if day<=CUT else None;p=pitraw.get(fid) if day<=CUT else None;st=starters.get(fid) if day<=CUT else None;r=v1.get(fid);sup=v2.get(fid)
   if b and (b.get('date'),b.get('home'),b.get('away'))!=(day,g['home'],g['away']):raise ValueError('old batter identity mismatch')
   if p and (p.get('date'),p.get('home'),p.get('away'))!=(day,g['home'],g['away']):raise ValueError('old pitcher identity mismatch')
   for side in ('home','away'):
    oldps=((p or {}).get('data') or {}).get(side,[]); lineup=prior.lineup(b,side); starter=prior.starter_from(p,st,side);relief=None
    if oldps:
     rs=[]
     for x in oldps[1:]:
      outs=prior.innings_outs(x.get('inn'))
      if not x.get('pcode') or outs is None:rs=None;break
      rs.append({'player_id':str(x['pcode']),'outs':outs,'source_bf_raw':x.get('bf')})
     if rs is not None:relief={'appearances':len(rs),'outs':sum(x['outs'] for x in rs),'pitchers':rs,'source_bf_units':'unverified'}
    if fid in selections:
     line,sp=selections[fid]
     # If the most recent official lineup has ambiguous IDs, the projection is missing.
     lineup=[x['player_id'] for x in line[side]] if line else None
     starter=sp[side]['player_id'] if sp else None
    if r:
     ps=r['statistics']['pitchers'][side]; q=r['quality']['pitchers'][side]
     if len([x for x in ps if x['is_starter']])==1 and all(integer(x['outs']) for x in ps) and q['team_equals_individual_outs'] is True and sum(x['outs'] for x in ps)==q['team_outs']:
      relief_rows=[x for x in ps if not x['is_starter']];relief={'appearances':len(relief_rows),'outs':sum(x['outs'] for x in relief_rows),'pitchers':[{'player_id':x['player_id'],'outs':x['outs']} for x in relief_rows],'source_bf_units':'unverified','provenance':{'box':ev(r['evidence']['box'])}}
     br=r['statistics']['batters'][side];bs=sup['batters'][side]
     if len(br)!=len(bs):raise ValueError('batter index length mismatch')
     for i,(x,y) in enumerate(zip(br,bs)):
      if y['v1_row_index']!=i or y['name']!=x['name'] or y['player_id']!=x['player_id'] or y['official_v1']!={k:x[k] for k in ('AB','H','R','RBI')}:raise ValueError('batter source row mismatch')
      if y['status']!='matched':
       counts['batter_incomplete_'+y['status']]+=1;incomplete.append({'fixture_id':fid,'date':day,'side':side,'row_index':i,'player_id':x['player_id'],'ab':x['AB'],'hit':x['H'],'bb':None,'reason':y['status'],'box':ev(r['evidence']['box'])});continue
      daily=y['daily_official_row'];de=y['daily_evidence'];pid=x['player_id']
      if not pid or str(de.get('query',{}).get('playerId'))!=pid or daily['date']!=r['game_date'] or any(daily[k]!=x[k] for k in ('AB','H','R','RBI')) or daily['BB']!=y['BB'] or not all(integer(z) for z in (x['AB'],x['H'],y['BB'])) or x['H']>x['AB']:raise ValueError('invalid matched batter')
      bh[pid].append({'fixture_id':fid,'date':day,'ab':x['AB'],'hit':x['H'],'bb':y['BB'],'hr':y.get('HR'),'provenance':{'box':ev(r['evidence']['box']),'daily':ev(de)}})
      counts['batter_complete']+=1;counts['batter_zero_ab_bb']+=int(x['AB']==0 and y['BB']==0)
     pp=sup['pitcher_id_supplement'][side]
     if len(ps)!=len(pp):raise ValueError('pitcher index length mismatch')
     linked=[]
     for i,(x,y) in enumerate(zip(ps,pp)):
      if y['v1_row_index']!=i or y['name']!=x['name'] or y['original_player_id']!=x['player_id'] or y['source_numeric_id_claim']!=x['source_numeric_id_claim']:raise ValueError('pitcher source row mismatch')
      pid=x['player_id'];profile=None
      if y['status']=='verified_official_profile':
       pid=y['supplement_player_id'];profile=ev(y['profile_evidence'])
       if not pid or pid!=x['source_numeric_id_claim'] or str(y['profile_evidence'].get('query',{}).get('playerId'))!=pid:raise ValueError('pitcher profile mismatch')
      elif y['status'] not in ('already_linked','no_official_numeric_claim'):raise ValueError('unknown pitcher status')
      linked.append((x,pid,profile,y['status']))
     duplicates={pid for pid,n in Counter(pid for _,pid,_,_ in linked if pid).items() if n>1}
     for x,pid,profile,status in linked:
      if not pid or pid in duplicates or not integer(x['outs']) or not integer(x['ER']):
       counts['pitcher_incomplete_duplicate_id' if pid in duplicates else 'pitcher_incomplete_missing_id_or_stats']+=1;continue
      prov={'box':ev(r['evidence']['box'])}
      if profile:prov['profile']=profile
      ph[pid].append({'fixture_id':fid,'date':day,'outs':x['outs'],'er':x['ER'],'r':x['R'],'provenance':prov});counts['pitcher_complete']+=1
    elif day<=CUT:
     for x in ((b or {}).get('data') or {}).get(side,[]):
      pid=x.get('code');ab=x.get('ab');hit=x.get('hit');bb=x.get('bb')
      if pid and all(integer(z) for z in (ab,hit,bb)) and hit<=ab:bh[str(pid)].append({'fixture_id':fid,'date':day,'ab':ab,'hit':hit,'bb':bb,'hr':x.get('hr')})
      elif pid:counts['old_batter_incomplete']+=1
     for x in oldps:
      outs=prior.innings_outs(x.get('inn'));er=x.get('er')
      if x.get('pcode') and integer(outs) and integer(er):ph[str(x['pcode'])].append({'fixture_id':fid,'date':day,'outs':outs,'er':er,'r':x.get('r')})
      elif x.get('pcode'):counts['old_pitcher_incomplete']+=1
    teams[g[side]].append({'id':fid,'date':day,'runs_for':g['hg' if side=='home' else 'ag'],'runs_against':g['ag' if side=='home' else 'hg'],'lineup':lineup,'starter_id':starter,'relief':relief,'home_venue':((b or {}).get('data') or {}).get('stadium') if side=='home' else None})
 # Check factorial identities, unchanged fixed core, component freshness.
 order=groups['all']
 if any([x['fixture_id'] for x in v]!=order for v in out.values()):raise ValueError('output cohort order mismatch')
 freshness=defaultdict(lambda:defaultdict(lambda:[[],0]));projections=defaultdict(Counter)
 for a,b,c,d in zip(*(out[k] for k in ('projected','official_batting_only','official_starter_only','official_batting_and_starter'))):
  fid=a['fixture_id'];day=dates[fid]; gs=['full709','original_193' if fid in set(groups['original_193']) else 'new_516','target_on_or_before_2026_07_26' if day<=CUT else 'target_after_2026_07_26']
  for row in (a,b,c,d):
   for k,v in bases[fid]['features'].items():
    if not any(k.endswith('_'+x) for x in ('lineup_strength','starter_strength','bullpen_load')) and row['features'][k]!=v:raise ValueError('nonplayer baseline changed')
  for side in ('home','away'):
   if a['features'][side+'_starter_strength']!=b['features'][side+'_starter_strength'] or a['features'][side+'_lineup_strength']!=c['features'][side+'_lineup_strength'] or b['features'][side+'_lineup_strength']!=d['features'][side+'_lineup_strength'] or c['features'][side+'_starter_strength']!=d['features'][side+'_starter_strength'] or len({json.dumps(x['features'][side+'_bullpen_load'],sort_keys=True) for x in (a,b,c,d)})!=1:raise ValueError('factorial mismatch')
   for name,row in [('projected',a),('official',d)]:
    fv=row['features'];profiles=fv[side+'_lineup_strength']['value']; pitcher=fv[side+'_starter_strength']['value']; bullpen=fv[side+'_bullpen_load']
    for group in gs:
     projections[group][name+'_lineup_selected_sides']+=int(profiles is not None);projections[group][name+'_starter_selected_sides']+=int((a['projection'][side+'_starter']['player_id'] is not None) if name=='projected' else True)
     for kind,histories in [('batter',[x['history'] for x in profiles] if profiles else [None]*9),('pitcher',[(pitcher or {}).get('appearances')]),('bullpen',[bullpen['value']['games'] if bullpen['value'] else None])]:
      for hist in histories:
       slot=freshness[group][name+'_'+kind]
       usable=hist
       if kind=='batter' and hist:usable=[x for x in hist if x['ab']+x['bb']>0]
       if kind=='pitcher' and hist:usable=[x for x in hist if x['outs']>0]
       if usable:
        latest=max(x['date'] for x in usable);slot[0].append((date.fromisoformat(day)-date.fromisoformat(latest)).days)
       else:
        slot[1]+=1
        if hist:projections[group][name+'_'+kind+'_zero_denominator_histories']+=1
 outsha={k:save('fresh-history-2026-'+k+'.jsonl.gz',v) for k,v in out.items()};outsha['incomplete_batters']=save('fresh-history-2026-incomplete-batters.jsonl.gz',incomplete)
 manifest={'schema':'kbo-fresh-history-input-v1','status':'retrospective_only_no_scoring_no_model_fit','protocol_commit':'1a15ab92da2d26a1744600716a44dcdf4f6bc37b','source_commit':COMMIT,'input_checks':checks,'cohorts':{'all':709,'original_193':193,'new_516':516,'excluded':17},'counts':dict(counts),'freshness':{g:{k:stat(*v) for k,v in sorted(c.items())} for g,c in sorted(freshness.items())},'freshness_definition':'Latest usable observation within the selected last ten complete records; AB+BB>0 for batter, outs>0 for pitcher. Zero-only histories count missing for usable freshness but remain observed in the input. Bullpen zero outs is usable.','selection_counts':{g:dict(v) for g,v in projections.items()},'outputs_sha256':outsha,'point_in_time_verified':0,'availability':'completed_at and top-level received_at null; source observations retain actual 2026-11-03 receipts; prior calendar dates assumed completed','policy':['Four outputs share the exact fixed 709 games and identical non-player features.','For each target, all same-day histories excluded; latest 10 complete observed games, including zero-PA observations.','Matched AB/H/pure BB required; BB_plus_HBP is never used.','Duplicate pitcher IDs per game/side excluded from personal history; relief outs retain unidentified pitchers when team-outs reconciliation succeeds.','Official target lineups and schedule starters are retrospective assumptions; prior lineup and five-starter selector use strictly earlier dates.','Incomplete records, unavailable identities, missing bullpen coverage and zero denominators remain unknown; no zero imputation.','The archived feature adapter requires an explicit separate scenario integration; no fitting or performance results.']}
 mp=HERE/'fresh-history-2026-manifest.json';tmp=mp.with_suffix('.json.tmp');tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8',newline='\n');os.replace(tmp,mp)
 print(json.dumps({'counts':dict(counts),'outputs_sha256':outsha,'freshness':manifest['freshness'],'selection_counts':manifest['selection_counts']},ensure_ascii=False))
if __name__=='__main__':main()
