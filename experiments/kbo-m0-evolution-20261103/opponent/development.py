"""2025-only candidate selection; commit outputs before any final training."""
import json,sys,hashlib
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent));import candidate as c
P=Path(__file__).resolve().parent;OUT=P/'development'

def main():
 data=c.core.load();train,val=c.core.development(data);ref=c.core.baseline_reference();baseline=ref['development_predictions'];replayed=c.core.predict_baseline(ref['development_model'],val)
 if [x['fixture_id'] for x in baseline]!=[x['fixture_id'] for x in replayed]:raise ValueError('baseline fixture mismatch')
 err=max(abs(p['probabilities_3way'][k]-q['probabilities_3way'][k]) for p,q in zip(baseline,replayed) for k in ('home','draw','away'))
 if err>1e-10:raise ValueError('baseline replay mismatch')
 models={};predictions={'baseline':baseline}
 for name in sorted(c.CONFIGS):
  print('fit development',name,flush=True);model=c.fit(train,data['labels'],name);models[name]=model;predictions[name]=c.predict(model,val)
 result=c.core.choose(predictions,data['labels']);result.update({'fit_seasons':[2023,2024],'fit_n':len(train),'selection_season':2025,'selection_n':len(val),'baseline_replay_max_abs_probability_error':err,'configurations':c.CONFIGS,'used2026_outcomes':False,'note':'retrospective, prior results known, no prices or test labels read'})
 c.core.dump(OUT/'models.json',models);c.core.dump(OUT/'predictions.json',predictions);c.core.dump(OUT/'selection.json',result)
 print(json.dumps({'selected':result['selected'],'ranking':result['ranking'],'baseline_replay_max_abs_probability_error':err},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
