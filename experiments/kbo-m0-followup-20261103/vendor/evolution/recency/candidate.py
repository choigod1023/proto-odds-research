"""Predeclared recency candidates; all inputs are copied before feature changes."""
import math, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'common'))
import core

CONFIGS = {
 'decay30': {'half_life':30, 'shrink':False, 'ordered':False},
 'decay90': {'half_life':90, 'shrink':False, 'ordered':False},
 'shrink': {'half_life':None, 'shrink':True, 'ordered':False},
 'ordered_decay30_shrink': {'half_life':30, 'shrink':True, 'ordered':True},
}
ORDER_WEIGHTS = [4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9]
OBP_PRIOR, OBP_PA, ERA_PRIOR, ERA_OUTS = .33, 20., 4.5, 27.

def weight(event_date, item, half_life):
 days = (date.fromisoformat(event_date) - date.fromisoformat(item['date'])).days
 if days <= 0: raise ValueError('history must precede game date')
 return 1. if half_life is None else 2. ** (-days / half_life)

def transform(row, config_id):
 cfg=CONFIGS[config_id]; out=core.normalize(row); day=row['event_date']
 for side in ('home','away'):
  name=side+'_lineup_strength'; source=row['raw_features'][name]['value']
  # Keep the original missing flag if no valid exposure exists anywhere.
  if source is not None and row['features'][name]['value'] is not None:
   samples=[]
   for person in source:
    h=pa=0.
    for x in person.get('history') or []:
     if core.legacy.adapter.nonnegative(x.get('ab'),x.get('hit'),x.get('bb')) and x['hit']<=x['ab']:
      w=weight(day,x,cfg['half_life']);h+=w*(x['hit']+x['bb']);pa+=w*(x['ab']+x['bb'])
    samples.append((h,pa))
   if cfg['ordered']:
    if len(samples)!=9: raise ValueError('ordered lineup must have nine slots')
    vals=[(h+OBP_PRIOR*OBP_PA)/(pa+OBP_PA) for h,pa in samples]
    value=sum(v*w/4.3 for v,w in zip(vals,ORDER_WEIGHTS))/9
   else:
    h=sum(x[0] for x in samples);pa=sum(x[1] for x in samples)
    if cfg['shrink']: h+=OBP_PRIOR*OBP_PA*len(samples);pa+=OBP_PA*len(samples)
    value=h/pa if pa else None
   out['features'][name]['value']=value
  name=side+'_starter_strength'; source=row['raw_features'][name]['value']
  if source is not None and row['features'][name]['value'] is not None:
   er=outs=0.
   for x in source['appearances']:
    if core.legacy.adapter.nonnegative(x.get('er'),x.get('outs')):
     w=weight(day,x,cfg['half_life']);er+=w*x['er'];outs+=w*x['outs']
   if cfg['shrink']: er+=ERA_PRIOR*ERA_OUTS/27.;outs+=ERA_OUTS
   out['features'][name]['value']=27*er/outs if outs else None
 return out

def fit(raw_rows,labels,config_id):
 if config_id not in CONFIGS: raise ValueError('unknown candidate')
 rows=[transform(x,config_id) for x in raw_rows]
 model=core.legacy.fit(rows,{x['fixture_id']:labels[x['fixture_id']] for x in rows},'M0',10.)
 return {'config_id':config_id,'config':CONFIGS[config_id].copy(),'estimator':model}

def predict(model,raw_rows):
 if model['config'] != CONFIGS[model['config_id']]: raise ValueError('model configuration mismatch')
 return core.legacy.predict(model['estimator'],[transform(x,model['config_id']) for x in raw_rows])
