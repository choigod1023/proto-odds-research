"""Choose on validation, refit once, freeze outcome-free test predictions."""
import datetime,json,hashlib
from pathlib import Path
import numpy as np
import model
ROOT=Path(__file__).resolve().parent

def dump(path,obj):path.write_bytes((json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8'))
def main():
 games,excluded=model.load_games();feats=model.history_features(games);train,val,test=model.partition(games)
 if min(len(train),len(val),len(test))==0:raise RuntimeError('empty fold')
 selected={}; diagnostics={};fitted={}
 for candidate,mode in [(False,'team_only'),(True,'lineup')]:
  vals=[]
  for alpha in (1,10,100):
   fit=model.fit(train,feats,alpha,candidate,datetime.date(2026,1,1));rates,_,_=model.predict(fit,val,feats)
   vals.append({'alpha':alpha,'score_logloss':model.score_loss(val,rates)})
  winner=min(vals,key=lambda x:(x['score_logloss'],x['alpha']));selected[mode]=winner['alpha'];diagnostics[mode]=vals
  fitted[mode]=model.fit(train+val,feats,winner['alpha'],candidate,datetime.date(2026,7,1))
 predictions={}
 for mode,fit in fitted.items():predictions[mode]=model.predict(fit,test,feats)
 rows=[]
 for i,g in enumerate(test):
  features=feats[g['id']];fb=json.dumps(features,separators=(',',':')).encode('utf8')
  row={'fixture_id':g['id'],'kickoff_kst':g['kickoff'],'team_ids':g['teams'],'features':features,'features_sha256':hashlib.sha256(fb).hexdigest(),'models':{}}
  for mode,(rates,p,unknown) in predictions.items():
   row['models'][mode]={'rate':[float(v) for v in rates[i]],'p':[float(v) for v in p[i]],'pick':int(np.argmax(p[i])),'unknown_teams':unknown[i]}
  rows.append(row)
 out=ROOT/'frozen';out.mkdir(exist_ok=True)
 (out/'test-predictions.jsonl').write_bytes(b''.join((json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf8') for r in rows))
 params={k:{'alpha':v['alpha'],'teams':v['teams'],'coefficients':[float(x) for x in v['coeff']],'optimizer_message':v['optimizer_message']} for k,v in fitted.items()}
 dump(out/'model-parameters.json',params)
 dump(out/'validation-selection.json',{'alpha_grid':[1,10,100],'selected':selected,'validation_score_logloss':diagnostics,'folds':{'train':len(train),'validation':len(val),'test':len(test)},'excluded':excluded,'test_results_scored':False})
 print(json.dumps({'folds':[len(train),len(val),len(test)],'selected_alpha':selected,'validation':diagnostics,'excluded':excluded},ensure_ascii=True))
if __name__=='__main__':main()
