"""Select alpha on validation and freeze DC test predictions, without outcomes."""
import datetime,hashlib,json
from pathlib import Path
import numpy as np
import dc_model as d
ROOT=Path(__file__).resolve().parent

def write(p,x):p.write_bytes((json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
def validate_prior():
 protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8-sig'))
 checks=[(d.PRIOR/'model.py','original_model_sha256'),(d.PRIOR/'frozen/test-predictions.jsonl','prior_prediction_sha256'),(d.PRIOR/'results/metrics.json','prior_metrics_sha256')]
 for p,key in checks:
  if hashlib.sha256(p.read_bytes()).hexdigest()!=protocol[key]:raise ValueError('prior artifact mismatch '+p.name)
 return protocol

def main():
 validate_prior();games,excluded=d.base.load_games();features=d.base.history_features(games);train,val,test=d.base.partition(games)
 original=[json.loads(l) for l in (d.PRIOR/'frozen/test-predictions.jsonl').read_bytes().splitlines()];d.require_cohort(test,original,features)
 if [len(train),len(val),len(test)]!=[273,120,128]:raise ValueError('unexpected fold size')
 selected={};diagnostics={};fits={}
 for candidate,mode in [(False,'team_only'),(True,'lineup')]:
  choices=[]
  for alpha in (1,10,100):
   fit=d.fit(train,features,alpha,candidate,datetime.date(2026,1,1));rates,_,_,min_tau=d.predict(fit,val,features);choices.append({'alpha':alpha,'validation_score_logloss':d.score_loss(val,rates,fit['rho']),'train_rho':fit['rho'],'validation_min_tau':min_tau})
  best=min(choices,key=lambda x:(x['validation_score_logloss'],x['alpha']));selected[mode]=best['alpha'];diagnostics[mode]=choices
  fits[mode]=d.fit(train+val,features,best['alpha'],candidate,datetime.date(2026,7,1))
 predictions={mode:d.predict(fit,test,features) for mode,fit in fits.items()};rows=[]
 for i,(g,prior) in enumerate(zip(test,original)):
  row={'fixture_id':g['id'],'kickoff_kst':g['kickoff'],'team_ids':g['teams'],'features_sha256':prior['features_sha256'],'models':{}}
  for mode,(rates,p,unknown,_) in predictions.items():
   tau=d.all_tau(rates[i:i+1],fits[mode]['rho'])
   row['models'][mode]={'rates':[float(v) for v in rates[i]],'p':[float(v) for v in p[i]],'pick':int(np.argmax(p[i])),'unknown_teams':unknown[i],'min_tau':float(tau.min())}
  rows.append(row)
 out=ROOT/'frozen';out.mkdir(exist_ok=True)
 (out/'test-predictions.jsonl').write_bytes(b''.join((json.dumps(r,sort_keys=True,separators=(',',':'))+'\n').encode() for r in rows))
 write(out/'parameters.json',{k:{'teams':v['teams'],'theta':[float(x) for x in v['theta']],'candidate':v['candidate'],'alpha':v['alpha'],'rho':v['rho'],'rho_bounds':v['rho_bounds'],'optimizer_message':v['optimizer_message']} for k,v in fits.items()})
 write(out/'selection.json',{'selected_alpha':selected,'validation':diagnostics,'folds':[len(train),len(val),len(test)],'excluded':excluded,'test_results_scored':False})
 print(json.dumps({'folds':[len(train),len(val),len(test)],'selected':selected,'validation':diagnostics,'refit_rho':{k:v['rho'] for k,v in fits.items()}}))
if __name__=='__main__':main()
