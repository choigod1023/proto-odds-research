"""Read-only KBO archive normalization and uncapped settlement validation. Stdlib only."""
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal
from datetime import datetime
import json, gzip, hashlib, math
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
SRC=ROOT.parent/'baseball-information-stepchange-20261029/experiments/baseball-information-stepchange-20261029/kbo'
OPS=ROOT/'experiments/operating-ledger-comparison-20261029/comparison.json'
def read(p):
 if p.name.endswith('.jsonl.gz'):
  with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(s) for s in f if s.strip()]
 return json.loads(p.read_text(encoding='utf8'))
def dump(name,obj):
 (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
def payout(odds,hit,refund=False):
 return Decimal(0) if refund else Decimal(str(odds))-1 if hit else Decimal(-1)
def describe(rr):
 if not rr:return {'n':0}
 total=sum((Decimal(str(r['net_per_unit'])) for r in rr),Decimal(0))
 months=defaultdict(Decimal)
 for r in rr:months[r['kickoff'][:7]]+=Decimal(str(r['net_per_unit']))
 return dict(n=len(rr),events=len({r['event_id'] for r in rr}),date_from=min(r['kickoff'][:10] for r in rr),date_to=max(r['kickoff'][:10] for r in rr),wins=sum(r.get('hit') is True for r in rr),refunds=sum(r.get('refund',False) for r in rr),net_units=float(total),stake_krw=len(rr)*10000,net_krw=int(total*10000),roi=float(total/len(rr)),monthly_net_krw={k:int(v*10000) for k,v in sorted(months.items())})
records=[];exclusions=[];cohorts=[];checks=Counter();source_paths=set()
slim=read(ROOT/'data/model_candidates/kbo/archived-reproduction.jsonl.gz')
slim_index={(r['role'],r['source_fixture_id']):r for r in slim}
frozen=read(SRC/'frozen-selection.json');results=read(SRC/'results.json')
assert frozen['heads']['primary']=='e0:residual:0.03'
for role,count in [('validation',712),('evaluation',198)]:
 p=SRC/f'{role}-ledger.jsonl.gz';source_paths.add(p)
 rows=read(p);assert len(rows)==count
 for i,r in enumerate(rows):
  cohort=f'frozen{count}_{role}_'+('selection_2024' if role=='validation' else 'reused_2026')
  event=r['source_fixture_id'];date=r['date'];refund=r['hg']==r['ag']
  sr=slim_index[(role,event)]
  assert (r['hg'],r['ag'],date)==(sr['hg'],sr['ag'],sr['date'])
  prices=r.get('prices',r.get('historical_prices'));order=r.get('order',['home','away'])
  cohorts.append(dict(cohort=cohort,event_id=event,league='KBO',kickoff=date,home=r['home'],away=r['away'],home_score=r['hg'],away_score=r['ag'],prices=prices,order=order,voters=r.get('voters'),vote_counts=r.get('vote_counts'),saved_class_picks=r.get('picks'),source_file=str(p.resolve()),source_row_1based=i+1))
  for model,slim_key in [('e0:residual:0.03','expected'),('baseline','baseline_expected'),('market_null','market_expected')]:
   a=r['candidates'][model];ph=a['probability'];side='home' if ph>=.5 else 'away';odds=prices[order.index(side)]
   hit=not refund and ((side=='home')==(r['hg']>r['ag']))
   net=payout(odds,hit,refund)
   assert side==a['side'] and odds==a['price']
   assert int(net*10000)==a['profit_krw'] and int(hit)==a['hit']
   assert a['settled']==int(not refund) and a['stake_krw']==10000
   sa=sr[slim_key];assert sa==a
   selected_p=ph if side=='home' else 1-ph
   assert .5<=selected_p<1
   records.append(dict(cohort=cohort,league='KBO',model=model,event_id=event,kickoff=date,kickoff_precision='date_only',pick=side,odds=float(odds),net_per_unit=float(net),probability=selected_p,home_probability=ph,hit=hit,refund=refund,home=r['home'],away=r['away'],home_score=r['hg'],away_score=r['ag'],source_file=str(p.resolve()),source_row_1based=i+1,provenance_limits=['Retrospective research probabilities; pregame availability and executable quote times unproven.','Source probability denotes HOME; exported probability denotes selected outcome.','Evaluation reused previously; not untouched holdout.']+(['Validation baseline is pure Shin market; no historical votes.'] if role=='validation' else ['Evaluation baseline is Shin market90 + saved author vote10; not exact deployed replay.'])))
   checks['score_odds_profit_and_slim_exact_match']+=1
  if role=='evaluation':
   assert r['voters']>0 and sum(r['vote_counts'].values())==r['voters']
   assert abs(r['baseline_probability']-(.9*r['market_probability']+.1*r['vote_counts'].get('home',0)/r['voters']))<1e-12
   side=r['picks']['tipster_vote'];odds=prices[order.index(side)];hit=not refund and ((side=='home')==(r['hg']>r['ag']))
   records.append(dict(cohort=cohort,league='KBO',model='tipster_vote_saved_choice',event_id=event,kickoff=date,kickoff_precision='date_only',pick=side,odds=float(odds),net_per_unit=float(payout(odds,hit,refund)),hit=hit,refund=refund,voters=r['voters'],vote_share=r['vote_counts'].get(side,0)/r['voters'],source_file=str(p.resolve()),source_row_1based=i+1,provenance_limits=['Saved majority-vote choice; vote_share is not a calibrated probability.','Retrospective reused cohort; pregame input/quote availability unproven.']))
   checks['saved_reference_90_10_formula_matches']+=1
 # Match published uncapped aggregates without re-fitting or picking a new winner.
 for model in ['e0:residual:0.03','baseline','market_null']:
  rr=[x for x in records if x['cohort']==cohort and x['model']==model];s=describe(rr);expected=results['candidates'][model][role]
  assert s['n']==expected['n'] and s['net_krw']==expected['net_krw'] and abs(s['roi']-expected['roi'])<1e-12
  checks['published_aggregate_exact_matches']+=1
ops=read(OPS);source_paths.add(OPS)
assert ops['monthly_cap_applied'] is False
for i,e in enumerate(ops['events']):
 if e['fixture']['league']!='KBO':continue
 for model,a in e['arms'].items():
  if a['status']!='selected' or e['label_status']!='settled':
   exclusions.append(dict(event_id=e['event_id'],model=model,status=a['status'],label_status=e['label_status'],reason=a.get('reason')));continue
  official=e['official'];refund=official['home_score']==official['away_score'];side={'\ud648':'home','\uc6d0\uc815':'away'}[a['selection']]
  winner='home' if official['home_score']>official['away_score'] else 'away'
  hit=not refund and side==winner
  row=dict(cohort='saved_operating_evaluation_replay_2026_10',league='KBO',model=model,event_id=e['event_id'],kickoff=e['kickoff'],kickoff_precision='aware_timestamp',pick=side,odds=float(a['odds']),net_per_unit=float(payout(a['odds'],hit,refund)),hit=hit,refund=refund,home_score=official['home_score'],away_score=official['away_score'],source_file=str(OPS.resolve()),source_pointer=f'/events/{i}/arms/{model}',snapshot_id=e['snapshot_id'],source_result_observed_at=official['observed_at'],source_as_of=e['as_of'],source_captured_at=e['captured_at'],provenance_limits=['Derived comparison of saved input snapshots; capture_kind=retrospective_saved_input_replay.','fixed91 uses current saved electorate, not exact historical deployed-policy replay.','Proto saved result is not independent official authentication.','Result observed_at is not actual settlement time; no settled_at/available_at assigned.'])
  if a.get('probability') is not None:row['probability']=a['probability']
  records.append(row)
# No inferred actual settlement, receipt, or availability timestamps.
keys=[(r['cohort'],r['model'],r['event_id']) for r in records];assert len(keys)==len(set(keys))
for r in records:
 assert all(k in r for k in ('cohort','league','model','event_id','kickoff','odds','net_per_unit','source_file','provenance_limits'))
 assert math.isfinite(r['odds']) and r['odds']>1 and math.isfinite(r['net_per_unit'])
 datetime.fromisoformat(r['kickoff']);assert 'settled_at' not in r and 'available_at' not in r
 if 'probability' in r:assert 0<=r['probability']<=1
 assert abs(float(payout(r['odds'],r['hit'],r['refund']))-r['net_per_unit'])<1e-12
checks['unique_valid_normalized_records']=len(records)
summary={}
for c in sorted({r['cohort'] for r in records}):
 summary[c]={m:describe([r for r in records if r['cohort']==c and r['model']==m]) for m in sorted({r['model'] for r in records if r['cohort']==c})}
evalrows=[r for r in records if 'frozen198_' in r['cohort']]
periods={label:{m:describe([r for r in evalrows if r['model']==m and (r['kickoff']<'2026-07-01')==early]) for m in sorted({r['model'] for r in evalrows})} for label,early in [('pre_july',True),('post_july',False)]}
rawcohort=read(SRC/'sources/cohort.jsonl.gz');assert Counter(r['role'] for r in rawcohort)==Counter(train=710,validation=712,evaluation=198)
source_paths.update([SRC/'sources/cohort.jsonl.gz',SRC/'frozen-selection.json',SRC/'results.json',SRC/'PROTOCOL.md',SRC/'source-audit.json',ROOT/'data/model_candidates/kbo/archived-reproduction.jsonl.gz',ROOT/'data/model_candidates/kbo/frozen-e0.json',ROOT/'docs/research/2026-10-25-research-scope-correction.md',SRC.parent.parent.parent/'scripts/kbo_opponent_dynamic_research.py'])
manifest=[]
for p in sorted(source_paths):
 assert p.exists(),p
 b=p.read_bytes();manifest.append(dict(path=str(p.resolve()),bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
metadata=dict(monthly_cap_applied=False,stake_krw=10000,settlement='Single fixed unit each selected row; win odds-1, loss -1, draw refund 0. Refund stake remains in ROI denominator.',uncapped_summary=summary,evaluation_subperiods=periods,checks=dict(checks),source_manifest=manifest,exclusions=exclusions,training=dict(n=710,date_from=min(r['date'] for r in rawcohort if r['role']=='train'),date_to=max(r['date'] for r in rawcohort if r['role']=='train'),selection_fit_years=['2023'],final_fit_years=['2023','2024'],coefficient_fit_n=1422),limitations=['No unused external holdout. Evaluation198 already reused in earlier research.','Validation baseline uses pure market while evaluation baseline uses market90/vote10.','No actual settlement time inferred; parent owns day+1/day+2 sensitivity.','No cap replay, filter selection, bootstrap, model refit or winner promotion executed here.','Saved operating KBO fixed91 available on two settled events only; third event fixed91 unavailable/no_valid_votes.'])
dump('records.json',{'records':records})
dump('cohorts.json',{'records':cohorts,'training_cohort_source':str((SRC/'sources/cohort.jsonl.gz').resolve()),'training_records':[r for r in rawcohort if r['role']=='train']})
dump('uncapped-validation.json',metadata)
dump('normalization-checks.json',dict(checks))
print(json.dumps({'output_records':len(records),'checks':dict(checks),'summary':summary,'evaluation_subperiods':periods},ensure_ascii=True,indent=2))
