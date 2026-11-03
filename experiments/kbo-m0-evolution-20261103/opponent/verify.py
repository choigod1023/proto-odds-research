"""Independently replay fixed development selection and final forecasts, no 2026 labels."""
import json,hashlib,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent));import candidate as c
P=Path(__file__).resolve().parent

def main():
 data=c.core.load();train,val=c.core.development(data);alltrain=c.core.final_training(data);selection=json.loads((P/'development/selection.json').read_bytes());pred=json.loads((P/'development/predictions.json').read_bytes());models=json.loads((P/'development/models.json').read_bytes());final=json.loads((P/'final/predictions.json').read_bytes());fm=json.loads((P/'final/model.json').read_bytes())
 check=c.core.choose(pred,data['labels']);assert selection['selected']==check['selected'] and selection['ranking']==check['ranking'];assert len(train)==1451 and len(val)==731 and len(alltrain)==2182
 for name,model in models.items():
  expected=c.predict(model,val);stored=pred[name];assert [x['fixture_id'] for x in expected]==[x['fixture_id'] for x in stored]
  assert max(abs(p['probabilities_3way'][k]-q['probabilities_3way'][k]) for p,q in zip(expected,stored) for k in ('home','draw','away'))<1e-12
  w,last=c.training_weights(train,c.CONFIGS[name]['decay_days']);assert np.isclose(w.sum(),1451) and model['weight_anchor_training_date']==last and abs(model['weight_sum']-1451)<1e-9
  spec=c.core.legacy.make_spec([c.core.normalize(x) for x in train]);assert model['spec']['mean']==spec['mean'] and model['spec']['scale']==spec['scale'] and model['spec']['hitters']==model['spec']['starters']==[]
 assert fm['selected']==final['selected']==selection['selected'] and fm['training_n']==2182 and fm['used2026_outcomes'] is False and final['used2026_outcomes'] is False
 reference=c.core.baseline_reference();maxerr=0.
 if selection['selected']=='baseline':
  expected=c.core.predict_baseline(reference['final_model'],data['test_actual']);observed=final['actual']
  assert [x['fixture_id'] for x in expected]==[x['fixture_id'] for x in observed];maxerr=max(abs(p['probabilities_3way'][k]-q['probabilities_3way'][k]) for p,q in zip(expected,observed) for k in ('home','draw','away'));assert maxerr<1e-10
 for kind in ('actual','projected'):
  output=final[kind];assert len(output)==709 and len({x['fixture_id'] for x in output})==709
  for row in output:
   p=row['probabilities_3way'];assert all(np.isfinite(v) and 0<=v<=1 for v in p.values()) and abs(sum(p.values())-1)<1e-12
 assert [x['fixture_id'] for x in final['actual']]==[x['fixture_id'] for x in final['projected']]
 print(json.dumps({'status':'ok','selected':selection['selected'],'development_candidates':4,'development_fixtures':731,'training_n':2182,'final_each':709,'baseline_final_max_probability_error':maxerr,'labels_2026_read':False}))
if __name__=='__main__':main()
