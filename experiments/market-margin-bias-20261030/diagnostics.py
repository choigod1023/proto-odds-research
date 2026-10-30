import collections,csv,json
from pathlib import Path
import numpy as np
from study import load,get_predictions,observations,NAMES
R=Path(__file__).resolve().parent
rows,_,_=load(); pred,coeff=get_predictions(rows);test=[r for r in rows if r['split']==2]
with (R/'league-comparison.csv').open('w',encoding='utf8',newline='') as f:
 w=csv.writer(f);w.writerow(['league','candidate','n','logloss','brier','accuracy','accuracy_delta','roi_delta','changed_2way','changed_3way'])
 for league in sorted(set(r['league'] for r in test)):
  rs=[r for r in test if r['league']==league]; base=observations(rs,pred['proportional'])
  for name in NAMES:
   a=observations(rs,pred[name]);changed=[r for r in rs if np.argmax(pred[name][r['id']])!=np.argmax(pred['proportional'][r['id']])];w.writerow([league,name,len(rs),a[:,2].mean(),a[:,3].mean(),a[:,0].mean(),(a[:,0]-base[:,0]).mean(),(a[:,1]-base[:,1]).mean(),sum(len(r['odds'])==2 for r in changed),sum(len(r['odds'])==3 for r in changed)])
with (R/'league-coefficients.csv').open('w',encoding='utf8',newline='') as f:
 w=csv.writer(f);w.writerow(['devig','league','home_offset','draw_offset','away_offset']);
 for m in sorted(coeff):
  for league,a in sorted(coeff[m].items()):w.writerow([m,league,*a])
x=json.loads((R/'results.json').read_text(encoding='utf8'))
with (R/'calibration.csv').open('w',encoding='utf8',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['dimension','group','probability_bin','n','predicted','observed']);w.writeheader();w.writerows(x['calibration'])
print('league tables and calibration rows:',len(x['calibration']))
