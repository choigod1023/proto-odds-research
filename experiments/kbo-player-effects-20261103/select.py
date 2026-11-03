"""Select penalties on 2024 and family on 2025; never loads 2026 labels."""
import json
from pathlib import Path
import research as r

def choose_penalty(results):
 best=None
 for penalty in r.PENALTIES:
  x=results[str(int(penalty))]['metrics']['logloss']
  if best is None or x<best[1]-1e-12 or abs(x-best[1])<=1e-12 and penalty>best[0]:best=(penalty,x)
 return best[0]

def choose_family(results):
 best=None
 for rank,name in enumerate(('M0','M1','M2')):
  m=results[name]['metrics'];candidate=(name,m['logloss'],m['brier'],rank)
  if best is None or candidate[1]<best[1]-1e-12 or abs(candidate[1]-best[1])<=1e-12 and (candidate[2]<best[2]-1e-12 or abs(candidate[2]-best[2])<=1e-12 and candidate[3]<best[3]):best=candidate
 return best[0]

def main():
 rows,labels,_=r.load_data();val24=[x for x in rows if x['season']==2024];fit23=r.cut_training(rows,(2023,),val24[0]['event_date']);val25=[x for x in rows if x['season']==2025];fit24=r.cut_training(rows,(2023,2024),val25[0]['event_date']);inner={};penalties={}
 for family in ('M1','M2'):
  inner[family]={}
  for alpha in r.PENALTIES:
   model=r.fit(fit23,labels,family,alpha);pred=r.predict(model,val24);inner[family][str(int(alpha))]={'metrics':r.metrics(pred,labels),'optimizer':{k:model[k] for k in ('iterations','gradient_max','convergence')}}
  penalties[family]=choose_penalty(inner[family]);print('selected 2024',family,penalties[family],flush=True)
 outer={}
 for family in ('M0','M1','M2'):
  model=r.fit(fit24,labels,family,penalties.get(family,10.));pred=r.predict(model,val25);outer[family]={'metrics':r.metrics(pred,labels),'model':model,'predictions':pred};print('2025',family,json.dumps(outer[family]['metrics']),flush=True)
 selected=choose_family(outer);out={'contract_sha256':'6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed','inner':{'train':len(fit23),'validation':len(val24),'validation_start':val24[0]['event_date'],'results':inner},'selected_player_penalties':penalties,'outer':{'train':len(fit24),'validation':len(val25),'validation_start':val25[0]['event_date'],'models':outer},'selected_family':selected,'selection_rule':'2025 logloss then Brier within 1e-12 then M0/M1/M2','outcomes_after_2025_read':False};r.dump(r.A/'selection.json',out);print('selected',selected,flush=True)
if __name__=='__main__':main()
