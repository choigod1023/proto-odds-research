"""Reselect corrected M2 on 2024 and compare to frozen v1 M0/M1 in 2025."""
import hashlib,importlib.util,json,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P));import research_v2 as m
r=m.v1
spec=importlib.util.spec_from_file_location('_original_selection_rules',P.parent/'select.py');rules=importlib.util.module_from_spec(spec);spec.loader.exec_module(rules)
V1_SHA='27c995c0d66bbc9a1e9c1a741735540adb21355ba76fbfac4eb4eff8a650eebb';V1_COMMIT='5dbddb0ea645ebf025dc5b7ce4c94ddca96ce35c'
def v1_selection():
 path=r.A/'selection.json';raw=path.read_bytes();blob=subprocess.check_output(['git','show',V1_COMMIT+':experiments/kbo-player-effects-20261103/artifacts/selection.json'])
 if hashlib.sha256(raw).hexdigest()!=V1_SHA or raw!=blob:raise ValueError('v1 selection changed')
 return json.loads(raw)
def main():
 m.verify_contract();old=v1_selection();rows,labels,_=r.load_data();v24=[x for x in rows if x['season']==2024];t23=r.cut_training(rows,(2023,),v24[0]['event_date']);v25=[x for x in rows if x['season']==2025];t24=r.cut_training(rows,(2023,2024),v25[0]['event_date']);grid={}
 for penalty in r.PENALTIES:
  model=m.fit(t23,labels,penalty);pred=m.predict(model,v24);grid[str(int(penalty))]={'metrics':r.metrics(pred,labels),'optimizer':{k:model[k] for k in ('iterations','gradient_max','convergence')}}
 alpha=rules.choose_penalty(grid);model=m.fit(t24,labels,alpha);pred=m.predict(model,v25);result={'metrics':r.metrics(pred,labels),'model':model,'predictions':pred};families={k:{'metrics':old['outer']['models'][k]['metrics']} for k in ('M0','M1')};families['M2']=result;selected=rules.choose_family(families)
 out={'correction_contract_sha256':m.CONTRACT_SHA,'original_selection_sha256':V1_SHA,'inner':{'train':len(t23),'validation':len(v24),'results':grid},'selected_player_penalty':alpha,'outer':{'train':len(t24),'validation':len(v25),'M0_and_M1_frozen':True,'families':families},'selected_family':selected,'outcomes_after_2025_read':False,'retrospective_correction_after_v1_2026_seen':True};r.dump(P/'selection.json',out);print(json.dumps({'penalty':alpha,'family':selected,'outer':{k:v['metrics'] for k,v in families.items()}},ensure_ascii=False))
if __name__=='__main__':main()
