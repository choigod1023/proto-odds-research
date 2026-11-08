"""Descriptive subgroups and paired week resampling, without model selection."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
import gzip,json,hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).parent
with gzip.open(P/'predictions.jsonl.gz','rt',encoding='utf8') as z:rows=[json.loads(x) for x in z]
def metrics(rr):
 if not rr:return None
 q=np.array([r['p'] for r in rr]); y=np.array([r['y'] for r in rr]); one=np.eye(q.shape[1])[y]
 days=defaultdict(float)
 for r in rr:days[r['day']]+=float(r['net'])
 cum=np.cumsum([days[x] for x in sorted(days)]); dd=np.max(np.maximum.accumulate(np.r_[0,cum])-np.r_[0,cum])
 return {'n':len(rr),'wins':sum(r['hit'] for r in rr),'hit':float(np.mean([r['hit'] for r in rr])),'roi':float(np.mean([float(r['net']) for r in rr])),'logloss':float(-np.log(q[np.arange(len(y)),y]).mean()),'brier':float(((q-one)**2).sum(axis=1).mean()),'mdd_units':float(dd)}
models=('market_devig','poisson_1x2','poisson_all','dc_all','kl_soft');families=('승무패','핸디캡','언더오버');summary={}
for model in models:
 for fam in families:
  subset=[r for r in rows if r['model']==model and r['family']==fam]; groups=defaultdict(list)
  for r in subset:
   y,m=map(int,r['day'][:7].split('-'));groups['all'].append(r);groups['league:'+r['league']].append(r);groups['year:'+str(y)].append(r);groups['quarter:'+str(y)+'-Q'+str((m-1)//3+1)].append(r);groups['month:'+r['day'][:7]].append(r);groups['league_year:'+r['league']+'/'+str(y)].append(r);groups['league_quarter:'+r['league']+'/'+str(y)+'-Q'+str((m-1)//3+1)].append(r)
  summary[model+'/'+fam]={k:metrics(v) for k,v in sorted(groups.items())}
contrasts={}; rng=np.random.default_rng(20261108)
for cohort in ('K_2025_2026','K_2026','EPL_2025_2026','라리가_2025_2026'):
 def include(r):
  return (r['league'].startswith('K리그') and (cohort=='K_2025_2026' or r['day'].startswith('2026'))) if cohort.startswith('K_') else r['league']==cohort.split('_')[0]
 for fam in families:
  cand=[r for r in rows if r['model']=='kl_soft' and r['family']==fam and include(r)]
  for baseline in ('market_devig','poisson_all','dc_all'):
   ref=[r for r in rows if r['model']==baseline and r['family']==fam and include(r)]
   assert [(r['event_id'],r['line'],r['odds'],r['y']) for r in cand]==[(r['event_id'],r['line'],r['odds'],r['y']) for r in ref]
   if not cand:continue
   weeks=defaultdict(list); changed=[]
   for c,b in zip(cand,ref):
    y=c['y']; delta=[int(c['hit'])-int(b['hit']),float(c['net'])-float(b['net']),-np.log(c['p'][y])+np.log(b['p'][y])]
    dt=date.fromisoformat(c['day']).isocalendar();weeks[(dt.year,dt.week)].append(delta)
    if c['pick']!=b['pick']:changed.append({'event_id':c['event_id'],'day':c['day'],'league':c['league'],'odds':c['odds'],'market_p':c['market_p'],'candidate_p':c['p'],'y':y,'baseline_pick':b['pick'],'candidate_pick':c['pick'],'baseline_hit':b['hit'],'candidate_hit':c['hit']})
   blocks=[np.array(v) for v in weeks.values()]; sums=np.array([b.sum(axis=0) for b in blocks]);lens=np.array([len(b) for b in blocks]);obs=sums.sum(axis=0)/lens.sum();boot=[];permut=[]
   for _ in range(1000):
    idx=rng.integers(len(blocks),size=len(blocks));boot.append(sums[idx].sum(axis=0)/lens[idx].sum()); signs=rng.choice([-1,1],size=len(blocks));permut.append((sums*signs[:,None]).sum(axis=0)/lens.sum())
   boot=np.array(boot);permut=np.array(permut); result={}
   for j,name in enumerate(('hit','roi','logloss')):
    result[name]={'difference_kl_minus_baseline':float(obs[j]),'ci95':np.quantile(boot[:,j],[.025,.975]).tolist(),'p_two_sided_week_sign_flip':float((1+np.count_nonzero(abs(permut[:,j])>=abs(obs[j])-1e-15))/1001)}
   contrasts[cohort+'/'+fam+'/'+baseline]={'n':len(cand),'weeks':len(blocks),'changed':len(changed),'baseline_win_to_kl_loss':sum(x['baseline_hit'] and not x['candidate_hit'] for x in changed),'baseline_loss_to_kl_win':sum(not x['baseline_hit'] and x['candidate_hit'] for x in changed),'metrics':result,'changed_details':changed}
# Holm family: 27 tests for each cohort = three markets x three baselines x three metrics.
for cohort in ('K_2025_2026','K_2026','EPL_2025_2026','라리가_2025_2026'):
 all_tests=[v for k,x in contrasts.items() if k.startswith(cohort+'/') for v in x['metrics'].values()]; ordered=sorted(all_tests,key=lambda x:x['p_two_sided_week_sign_flip']); prev=0
 for i,v in enumerate(ordered):
  prev=max(prev,min(1,(len(ordered)-i)*v['p_two_sided_week_sign_flip']));v['holm_p_within_cohort']=prev
# Fixed near-tie sensitivity: use deterministic hash among probabilities within 1e-8 of max.
def tie_pick(r):
 p=np.array(r['p']);idx=np.flatnonzero(p>=p.max()-1e-8)
 seed=int(hashlib.sha256(('20261108|'+r['event_id']+'|'+r['family']).encode()).hexdigest()[:16],16)
 return int(idx[seed%len(idx)])
ties={}
for model in ('market_devig','kl_soft'):
 rr=[r for r in rows if r['model']==model and r['family']=='언더오버' and r['league'].startswith('K리그')]
 diffs=[r for r in rr if tie_pick(r)!=r['pick']]
 ties[model]={'n':len(rr),'within_1e-8':int(sum(sum(np.array(r['p'])>=max(r['p'])-1e-8)>1 for r in rr)),'changed_by_seeded_tie_rule':len(diffs),'hit_delta':sum(int(tie_pick(r)==r['y'])-int(r['hit']) for r in rr),'net_delta':float(sum((Decimal(str(r['odds'][tie_pick(r)]))-1 if tie_pick(r)==r['y'] else Decimal(-1))-Decimal(r['net']) for r in rr))}
market=[r for r in rows if r['model']=='market_devig' and r['family']=='언더오버' and r['league'].startswith('K리그')]
kl=[r for r in rows if r['model']=='kl_soft' and r['family']=='언더오버' and r['league'].startswith('K리그')]
changed=[(a,b) for a,b in zip(market,kl) if a['pick']!=b['pick']]
ties['paired_market_vs_kl']={'changed_original':len(changed),'changed_after_seeded_tie_rule':sum(tie_pick(a)!=tie_pick(b) for a,b in zip(market,kl)),'changed_original_market_ties_1e-8':sum(abs(a['p'][0]-a['p'][1])<=1e-8 for a,b in changed),'changed_original_probability_gap_max':max(abs(a['p'][0]-a['p'][1]) for a,b in changed),'changed_original_all_odds_valid':all(all(float(x)>1 and np.isfinite(float(x)) for x in a['odds']) for a,b in changed)}
info=json.loads((P/'inventory.json').read_text(encoding='utf8'));num=info['grid15_numerical_only']
result={'period':'2025-01-01 through 2026-12-31 as available; retrospective and previously used','groups':summary,'contrasts':contrasts,'tie_sensitivity':ties,'numerical_grid15':{'n':len(num),'max_grid10_edge_mass':max(x['edge_mass_10'] for x in num),'max_grid15_tail_above_10':max(x['tail_mass_above_10_grid15'] for x in num),'max_market_probability_difference':max(x['market_probability_max_abs_diff'] for x in num)},'bootstrap':'1000 week-cluster resamples, seed 20261108; CIs nominal; randomized week sign-flip two-sided p; Holm per cohort across markets, baselines and hit/ROI/logloss (27 tests).'}
(P/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
