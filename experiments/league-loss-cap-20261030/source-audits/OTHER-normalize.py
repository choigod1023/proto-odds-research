"""Read-only source normalization and uncapped settlement; writes only this folder."""
import json, pathlib, hashlib, math, sys, pickle
from collections import defaultdict, Counter
from decimal import Decimal
from datetime import datetime
sys.stdout.reconfigure(encoding='utf8')
BASE=pathlib.Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')
OUT=BASE/'league-loss-cap-ablation-20261030/.local-test/OTHER'
records=[]; sources={}; checks=[]
def read(p):
 p=pathlib.Path(p); raw=p.read_bytes(); sources[str(p)]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
 return json.loads(raw) if p.suffix=='.json' else [json.loads(l) for l in raw.decode('utf8').splitlines() if l]
def lg(s): return {'K리그1':'KLeague1','K리그2':'KLeague2'}.get(s,s)
def add(dataset,cohort,league,model,eid,day,order,prices,pick,winner,source,prob=None,kickoff=None,**extra):
 if pick is None:return
 i=order.index(pick); odds=float(prices[i]); assert odds>1 and math.isfinite(odds)
 push=winner=='draw' and len(order)==2
 net=0. if push else float(Decimal(str(prices[i]))-1) if pick==winner else -1.
 k=kickoff or day
 if 'T' in str(k):
  dt=datetime.fromisoformat(str(k).replace('Z','+00:00'))
  if dt.tzinfo is None:k=day
 else:datetime.fromisoformat(k)
 row=dict(dataset=dataset,cohort=cohort,league=lg(league),model=model,event_id=str(eid),kickoff=str(k),match_date=day,odds=odds,net_per_unit=net,pick=pick,winner=winner,hit=False if push else pick==winner,refund=push,source_file=str(source),provenance_limits='Retrospective saved research; not frozen live operating predictions; historical purchase availability and actual settlement timestamp unproven',**extra)
 if prob is not None:
  assert math.isfinite(float(prob)) and 0<=prob<=1;row['probability']=float(prob)
 records.append(row)
# Yearly saved choices. Never reconstruct absent probabilities from odds/votes.
ybase=BASE/'year-model-hit-comparison-20261029/experiments/year-model-hit-20261029'
for variant in ['strict','standard-ot']:
 p=ybase/f'{variant}-results.json'; data=read(p); original=read(ybase/f'{variant}.jsonl'); index=defaultdict(list)
 for r in original:index[(r['league'],r['fixture_id'])].append(r)
 common={e['fixture_id'] for e in data['event_ledger'] if all(x is not None for x in e['picks'].values())}
 for e in data['event_ledger']:
  batch=index[(e['league'],e['fixture_id'])]; scores={(r['home_score'],r['away_score']) for r in batch};assert len(scores)==1
  h,a=scores.pop();winner='home' if h>a else 'away' if a>h else 'draw';assert winner==e['winner']
  assert any(r.get('proto_offer_evidence')=='archived_exact_fixture' and r.get('proto_odds')==e['prices'] for r in batch)
  for m,pick in e['picks'].items():
   add('year_'+variant,'selection' if e['match_date']<'2026-04-29' else 'evaluation',e['league'],f'year_{variant}:{m}',e['fixture_id'],e['match_date'],e['order'],e['prices'],pick,winner,p,kickoff=e.get('kickoff'),common_all_models=e['fixture_id'] in common,period_status='Chronological descriptive split, both halves previously seen; not independent holdout',pregame_certified=False,raw_input_file=str(ybase/f'{variant}.jsonl'))
 for scope in ['model_specific','common']:
  for m,s in data['comparison'][scope].items():
   chosen=[r for r in records if r['dataset']=='year_'+variant and r['model']==f'year_{variant}:{m}' and (scope!='common' or r['common_all_models'])]
   roi=sum(r['net_per_unit'] for r in chosen)/len(chosen);assert abs(roi-s['unit_roi'])<1e-12
   checks.append(dict(dataset='year_'+variant,scope=scope,model=m,n=len(chosen),recomputed_roi=roi,saved_roi=s['unit_roi'],passed=True))
# Saved K-league DC probabilities and decisions, full fixed catalog.
p=BASE/'kleague-decay-backtest-20261029/experiments/kleague-decay-20261029/results.json';d=read(p)
for e in d['ledger']:
 winner='home' if e['hg']>e['ag'] else 'away' if e['ag']>e['hg'] else 'draw';order=['home','draw','away']
 for m,v in e['models'].items():
  add('kleague_decay','evaluation',e['league'],'kleague_decay:'+m,e['fixture_id'],e['date'],order,e['prices'],v['pick'],winner,p,v['probabilities'][order.index(v['pick'])],period_status='2025 monthly walk-forward decay selection; 2026 previously exposed evaluation; monthly refit uses prior dates',pregame_certified=False,probability_semantics='Saved retrospective model probability for selected side')
# All saved retune candidates: validation selection and separate evaluation.
rbase=BASE/'baseball-league-retune-20261029/experiments/baseball-league-retune-20261029'
for league in ['kbo','mlb']:
 result=read(rbase/league/'results.json')
 for name in ['validation_predictions.jsonl','predictions.jsonl']:
  p=rbase/league/name;rows=read(p)
  for e in rows:
   winner='home' if e['hg']>e['ag'] else 'away' if e['ag']>e['hg'] else 'draw'
   probabilities=dict(e['candidate_probabilities']); probabilities.setdefault('baseline',e['baseline_probability']);probabilities.setdefault('market_null',e['market_probability'])
   policies=e.get('policies',e)
   for h in ['primary','hit_leader','profit_leader']:
    if isinstance(policies.get(h),dict):probabilities['selected_'+h]=policies[h]['probability']
   for m,ph in probabilities.items():
    pick='home' if ph>=.5 else 'away'
    add('baseball_retune','selection' if name.startswith('validation') else 'evaluation',e['league'],'baseball_retune:'+m,e['fixture_id'],e['date'],e['order'],e['prices'],pick,winner,p,ph if pick=='home' else 1-ph,kickoff=e.get('start') or e.get('kickoff'),eval_set=e.get('eval_set',e['role']),baseline_kind=e['baseline_kind'],period_status='2023 training; 2024 validation selection; 2023+2024 refit; exposed later evaluation, not fresh holdout',probability_semantics='Saved retrospective home probability, complemented when away selected',pregame_certified=False)
# Trusted local preparation cache has actual saved market vectors, result labels, primary prices.
p=BASE/'sport-model-tournament-20261025/.local-test/sport-tournament/events.pkl';raw=p.read_bytes();sources[str(p)]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)};df=pickle.loads(raw)
assert not df.duplicated(['kickoff','sport','league','home_team','away_team']).any()
for e in df.to_dict('records'):
 order=['home','draw','away'] if e['sport']=='sc' else ['home','away']; i=max(range(len(order)),key=lambda i:e['market'][i]);winner=order[int(e['y'])]
 expected=0 if e['outcome']==1 else 1 if e['outcome']==.5 else 2 if e['sport']=='sc' else 1
 assert int(e['y'])==expected and not(e['outcome']==.5 and e['sport']!='sc')
 day=e['date'].date().isoformat();eid='|'.join([e['sport'],e['league'],str(e['kickoff']),e['home_team'],e['away_team']])
 # Naive cache times are preserved as text only; date is used for kickoff to avoid guessing timezone.
 cohort='training' if e['year']==2023 else 'selection' if e['year']==2024 else 'evaluation' if e['year']==2025 else 'later_descriptive'
 add('archive_market',cohort,e['league'],'archive_market:saved_shin_argmax',eid,day,order,e['odds'],order[i],winner,p,e['market'][i],sport=e['sport'],home=e['home_team'],away=e['away_team'],source_kickoff_naive=str(e['kickoff']),period_status='2023 training, 2024 selection, 2025 reused evaluation, 2026 additional descriptive; saved market reference only; no tipster or learned model replay',probability_semantics='Saved Shin market-implied probability; not independently calibrated true probability',pregame_certified=False)
# NPB saved probability catalog and matched evaluation.
p=BASE/'baseball-league-fit-20261029/experiments/baseball-league-fit-20261029/predictions.jsonl';npb_rows=[r for r in read(p) if r['league']=='NPB']
npb_result=read(BASE/'baseball-league-fit-20261029/experiments/baseball-league-fit-20261029/results.json')
for e in npb_rows:
 probabilities=dict(e['new_probabilities']); probabilities['market']=e['market_home_probability'];probabilities['fixed91']=e['existing91_home_probability']
 for head,candidate in npb_result['leagues']['NPB']['selected'].items():probabilities['selected_'+head]=e['new_probabilities'][candidate]
 for m,ph in probabilities.items():
  pick='home' if ph>=.5 else 'away'
  add('npb_league_fit','evaluation',e['league'],'npb_league_fit:'+m,e['fixture_id'],e['date'],e['order'],e['prices'],pick,e['winner'],p,ph if pick=='home' else 1-ph,kickoff=e.get('kickoff'),probability_semantics='Saved retrospective home probability, complemented for away',period_status='2023 fit, 2024 aggregate validation selection; prior-year refit; exposed 2026 evaluation. Candidate per-game 2024 probabilities NOT recovered',pregame_certified=False)
 for m in ['market75_vote25','market50_vote50','tipster_vote']:
  add('npb_league_fit','evaluation',e['league'],'npb_league_fit:'+m,e['fixture_id'],e['date'],e['order'],e['prices'],e['picks'][m],e['winner'],p,kickoff=e.get('kickoff'),period_status='Exposed 2026 same matched events; no per-game 2024 selection probabilities',pregame_certified=False)
for row in records:row['cohort']=row['dataset']+'/'+row['cohort']

# Assertions are task verification, not a production test suite.
keys=[(r['dataset'],r['cohort'],r['league'],r['model'],r['event_id']) for r in records];assert len(keys)==len(set(keys))
assert all('settled_at' not in r and 'available_at' not in r for r in records)
def summary(sample):
 total=math.fsum(r['net_per_unit'] for r in sample);n=len(sample)
 return dict(n=n,events=len({r['event_id'] for r in sample}),first=min(r['match_date'] for r in sample),last=max(r['match_date'] for r in sample),wins=sum(r['hit'] for r in sample),pushes=sum(r['refund'] for r in sample),net_units=total,uncapped_roi=total/n,flat_10000_net_krw=round(total*10000),with_probability=sum('probability' in r for r in sample),full_aware_kickoff=sum('T' in r['kickoff'] for r in sample),source_proven_settled_at=0)
groups=defaultdict(list)
for r in records:groups[(r['dataset'],r['cohort'],r['league'],r['model'],r.get('eval_set',''))].append(r)
sums=[dict(dataset=k[0],cohort=k[1],league=k[2],model=k[3],eval_set=k[4],**summary(v)) for k,v in sorted(groups.items())]
manifest=dict(total_rows=len(records),source_files=sources,dataset_rows=dict(Counter(r['dataset'] for r in records)),league_rows=dict(Counter(r['league'] for r in records)),limitations=['Do not combine strict and standard-ot: nested overlapping yearly samples.','Do not combine archive, yearly, DC and retune events: overlapping research cohorts and distinct fixture IDs.','Yearly split 2026-04-29 is descriptive and not new untouched evaluation.','No source-proven actual settlement time or purchase availability is provided.','Archive dataset supplies saved market reference, not saved tipster/fixed91.','Only DC and retune have saved model probabilities; yearly probability omitted rather than fabricated.','These are uncapped flat singles; not two-leg, weekly-limited or monthly loss-capped ROI.'],checks=checks)
(OUT/'records.json').write_text(json.dumps({'records':records},ensure_ascii=False,separators=(',',':')),encoding='utf8')
(OUT/'uncapped-summary.json').write_text(json.dumps(sums,ensure_ascii=False,indent=2),encoding='utf8')
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in manifest.items() if k not in ['source_files','checks','league_rows']},ensure_ascii=False,indent=2))
for ds in ['year_strict','year_standard-ot']:
 rr=[r for r in records if r['dataset']==ds and r.get('common_all_models')];g=defaultdict(list)
 for r in rr:g[(r['league'],r['model'])].append(r)
 print(ds,'COMMON FULL-PERIOD')
 for k,v in sorted(g.items()):
  if k[1].split(':')[-1] in ['market','market90_vote10','tipster_vote']:print(k,summary(v))
print('ARCHIVE EVALUATION FOCUS')
for s in sums:
 if s['dataset']=='archive_market' and s['cohort']=='evaluation' and s['league'] in ['KLeague1','KLeague2','KBL','NBA','KOVO남','KOVO여','KBO','MLB','NPB']:print(s)
