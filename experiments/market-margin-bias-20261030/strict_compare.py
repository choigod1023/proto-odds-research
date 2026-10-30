import collections, datetime, hashlib, json
from pathlib import Path
import numpy as np
from study import probabilities, corrected, observations, independent, NAMES
R=Path(__file__).resolve().parent
study=json.loads((R/'results.json').read_text(encoding='utf8'))
source=(R/'inputs/strict-results.json').read_bytes(); ledger=json.loads(source)['event_ledger']; cutoff=study['split_boundaries'][1][:10]
records=[e for e in ledger if e['picks'].get('tipster_vote') and e['match_date']>cutoff]
rows=[{'id':e['fixture_id'],'odds':e['prices'],'y':e['order'].index(e['winner'])} for e in records]
assert len(set(r['id'] for r in rows))==len(rows)
weeks=collections.defaultdict(list)
for i,e in enumerate(records):weeks[str(datetime.date.fromisoformat(e['match_date']).isocalendar()[:2])].append(i)
w=list(weeks.values());rng=np.random.default_rng(20261030); samples=rng.integers(0,len(w),size=(2000,len(w)))
base={}
for baseline in ['market','market90_vote10']:
 b=[]
 for e in records:
  j=e['order'].index(e['picks'][baseline]); hit=int(e['order'][j]==e['winner']); b.append((hit,hit*e['prices'][j]-1))
 base[baseline]=np.array(b)
results={}
for name in NAMES:
 method=name.split('+')[0]; picks={}
 for e in records:
  p=probabilities(e['prices'],method)
  if '+' in name:p=corrected(p,study['coefficients'][method].get(e['league'],[0,0,0]))
  picks[e['fixture_id']]=p
 a=observations(rows,picks);assert np.array_equal(a,independent(rows,picks))
 comparisons={}
 for baseline in base:
  delta=a[:,:2]-base[baseline];boot=[]
  for sample in samples:
   idx=np.concatenate([w[j] for j in sample]);boot.append(delta[idx].mean(axis=0))
  boot=np.array(boot);p=min(1.,2*min((boot[:,0]<=0).mean(),(boot[:,0]>=0).mean()))
  comparisons[baseline]={'accuracy_delta':float(delta[:,0].mean()),'roi_delta':float(delta[:,1].mean()),'accuracy_ci':np.quantile(boot[:,0],[.025,.975]).tolist(),'roi_ci':np.quantile(boot[:,1],[.025,.975]).tolist(),'p_uncorrected':p}
 results[name]={'accuracy':float(a[:,0].mean()),'roi':float(a[:,1].mean()),'comparisons':comparisons}
for baseline in base:
 order=sorted(NAMES,key=lambda n:results[n]['comparisons'][baseline]['p_uncorrected']);adjusted=0.
 for rank,n in enumerate(order):
  c=results[n]['comparisons'][baseline];adjusted=max(adjusted,min(1.,(len(NAMES)-rank)*c['p_uncorrected']));c['p_holm']=adjusted
chosen=study['selected_by_validation_logloss'];sel=results[chosen]
report={'n':len(rows),'coverage':1.0,'weeks':len(w),'cutoff_exclusive':cutoff,'selected':chosen,'accuracy':sel['accuracy'],'roi':sel['roi'],'comparisons':sel['comparisons'],'all_candidates':results,'source_sha256':hashlib.sha256(source).hexdigest(),'note':'Strict PR402 actual votes; already exposed retrospective secondary cohort, no independently authenticated pregame receipt. Not a blind holdout.'}
(R/'strict-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps({k:report[k] for k in ['n','coverage','weeks','selected','accuracy','roi','comparisons']}))
