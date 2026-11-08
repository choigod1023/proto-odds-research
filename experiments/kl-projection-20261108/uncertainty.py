"""Paired calendar-week bootstrap; differences KL minus one-market Poisson."""
import json
from pathlib import Path
import numpy as np
p=Path(__file__).parent; rows=json.loads((p/'predictions.json').read_text(encoding='utf8')); rng=np.random.default_rng(20261108); out={}
for fam in ('승무패','핸디캡','언더오버'):
 a=[x for x in rows if x['model']=='poisson_1x2' and x['family']==fam and x['date'][:4]>='2025']; b=[x for x in rows if x['model']=='kl_soft' and x['family']==fam and x['date'][:4]>='2025']
 assert [(x['date'],x['league'],x['home'],x['away']) for x in a]==[(x['date'],x['league'],x['home'],x['away']) for x in b]
 from datetime import datetime
 weeks={}
 for x,y in zip(a,b):
  week=datetime.fromisoformat(x['date']).date().isocalendar()[:2]; yi=x['outcome']; d=[(y['pick']==yi)-(x['pick']==yi),y['profit']-x['profit'],-np.log(y['probabilities'][yi])+np.log(x['probabilities'][yi])]; weeks.setdefault(week,[]).append(d)
 blocks=[np.array(v) for v in weeks.values()]; estimates=[]
 for _ in range(3000):
  sample=rng.integers(len(blocks),size=len(blocks)); estimates.append(np.concatenate([blocks[i] for i in sample]).mean(axis=0))
 values=np.concatenate(blocks).mean(axis=0); intervals=np.quantile(estimates,[.025,.975],axis=0)
 out[fam]={'method':'calendar week cluster bootstrap 3000 seed 20261108','n':len(a),'weeks':len(blocks),'kl_minus_poisson_1x2':{name:{'estimate':float(values[i]),'ci95':[float(intervals[0,i]),float(intervals[1,i])]} for i,name in enumerate(['hit','roi','logloss'])}}
(p/'uncertainty.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
