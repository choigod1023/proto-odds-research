"""Refit corrected M2 and freeze new predictions; v1 files remain unchanged."""
import hashlib,json,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P));import research_v2 as m
r=m.v1;SEL='d28b4101f81fea02d4046dea7d05c47449fffb55';SHA='9fa9c300ce4e74c938eefcbdcb745a06fed3006f79fc59855f33fe557e0ff611';OLD='ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9'
def selection():
 raw=(P/'selection.json').read_bytes();blob=subprocess.check_output(['git','show',SEL+':experiments/kbo-player-effects-20261103/v2/selection.json'])
 if hashlib.sha256(raw).hexdigest()!=SHA or blob!=raw:raise ValueError('v2 selection freeze mismatch')
 old=(r.A/'predictions.json').read_bytes();oldblob=subprocess.check_output(['git','show','433d915aa52727de4af128cd9e614ca80aa8aa72:experiments/kbo-player-effects-20261103/artifacts/predictions.json'])
 if hashlib.sha256(old).hexdigest()!=OLD or old!=oldblob:raise ValueError('v1 predictions changed')
 return json.loads(raw)
def main():
 m.verify_contract();selected=selection();rows,labels,scenarios=r.load_data();train=r.cut_training(rows,(2023,2024,2025),scenarios['projected'][0]['event_date']);model=m.fit(train,labels,selected['selected_player_penalty']);pred={k:m.predict(model,rs) for k,rs in scenarios.items()};r.dump(P/'model.json',{'selection_commit':SEL,'selection_sha256':SHA,'selected_family':selected['selected_family'],'training_n':len(train),'model':model,'original_M0_M1_unchanged':True});r.dump(P/'predictions.json',{'selection_commit':SEL,'selection_sha256':SHA,'selected_family':selected['selected_family'],'original_prediction_sha256':OLD,'scenarios':pred,'counts':{k:len(v) for k,v in pred.items()},'2026_labels_read':False,'historical_receipt_proven':False,'not_blind_prior_2026_seen':True});print(json.dumps({'training_n':len(train),'counts':{k:len(v) for k,v in pred.items()},'selected_family':selected['selected_family'],'iterations':model['iterations'],'gradient_max':model['gradient_max']}))
if __name__=='__main__':main()
