"""Post-hoc descriptive contrast, no selection or refitting."""
import json
from collections import Counter
from pathlib import Path
import numpy as np
p=Path(__file__).parent
rows=json.loads((p/'predictions.json').read_text(encoding='utf8'))
def select(model):
 return [x for x in rows if x['model']==model and x['family']=='언더오버' and x['date'][:4]>='2025']
a=select('market_devig'); b=select('kl_soft')
assert len(a)==len(b)
different=[]; same=[]
for x,y in zip(a,b):
 assert (x['date'],x['league'],x['home'],x['away'],x['line'],x['odds'],x['outcome'])==(y['date'],y['league'],y['home'],y['away'],y['line'],y['odds'],y['outcome'])
 gap=abs(x['probabilities'][0]-x['probabilities'][1]); record={'date':x['date'],'league':x['league'],'home':x['home'],'away':x['away'],'line':x['line'],'odds':x['odds'],'market_probabilities':x['probabilities'],'kl_probabilities':y['probabilities'],'market_pick':x['pick'],'kl_pick':y['pick'],'outcome':x['outcome'],'market_win':x['pick']==x['outcome'],'kl_win':y['pick']==y['outcome'],'market_probability_gap':gap}
 (different if x['pick']!=y['pick'] else same).append(record)
def describe(v):
 gaps=[x['market_probability_gap'] for x in v]
 return {'n':len(v),'gap_min':min(gaps),'gap_median':float(np.median(gaps)),'gap_max':max(gaps),'gap_le_0_05':sum(g<=.05 for g in gaps),'gap_le_0_10':sum(g<=.10 for g in gaps)}
result={'scope':'post-hoc only, 2025-2026 UO; gap is absolute difference between two devig market probabilities; no rule selection', 'changed':describe(different),'unchanged':describe(same),'market_win_to_kl_loss':sum(x['market_win'] and not x['kl_win'] for x in different),'market_loss_to_kl_win':sum(not x['market_win'] and x['kl_win'] for x in different),'changed_matches':different}
(p/'market_change_diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
