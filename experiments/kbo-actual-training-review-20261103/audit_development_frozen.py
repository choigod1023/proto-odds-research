"""Replay 2025 frozen development from independent formulas without fitting."""
import copy,gzip,hashlib,json,math,subprocess,sys
from datetime import date,timedelta
from pathlib import Path
import numpy as np
import reference as ref

REPO=Path(r'C:\Users\user\Documents\Codex\kbtmodel');DATA=Path(r'C:\Users\user\Documents\Codex\kbtdata');SNAP=Path(r'C:\Users\user\AppData\Local\Temp\codex-kbo-model-0d7cffe-audit\experiments\kbo-actual-training-20261103');CODE='0d7cffe4173855c1e6b2ef5ebc9af3ba10c76ae5';DEV='0277fa14b2e7ee2f950d683cf0093f9f1b6d788d';SOURCE='58556de08c64604276de8682373d3ccc8d535d7c';P='experiments/kbo-actual-training-20261103/';D='experiments/kbo-actual-training-data-20261103/'
def blob(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(x):return hashlib.sha256(x).hexdigest()
def need(x,msg):
 if not x:raise ValueError(msg)
def adapt(r):return {'home_id':r['home_id'],'away_id':r['away_id'],'roster':r['roster'],'values':{k:v['value'] for k,v in r['features'].items()}}
def check_close(a,b,tol=1e-10):return abs(a-b)<=tol*max(1,abs(a),abs(b))
def main():
 for name in ('dataset.py','materialize.py','fit_final.py','pipeline.py','runner.py','workflow.py','source_adapter.py','development.py'):
  need((SNAP/name).read_bytes()==blob(REPO,CODE,P+name),'frozen code mismatch '+name)
 sys.path.insert(0,str(SNAP));import dataset,pipeline as p
 pairs,labels,target,manifest=dataset.load();need(manifest['training_by_year']=={'2023':723,'2024':728,'2025':731},'year count')
 for stage,selected in (('train',pairs),('test',target)):
  for sel in ('actual','projected'):
   src=[json.loads(x) for x in gzip.decompress(blob(DATA,SOURCE,D+'paired-'+stage+'-'+sel+'.jsonl.gz')).splitlines()];expected={x['fixture_id']:{k:x[k] for k in ('fixture_id','season','event_date','home_id','away_id','features','roster')} for x in src};actual={x['fixture_id']:x for x in selected[sel]}
   need(expected==actual,'normalized source mismatch '+stage+' '+sel)
 # Earlier regression: corrupting a selected hitter profile ID must be rejected.
 sample=next(x for x in pairs['actual'] if x['features']['home_lineup_strength']['value'] is not None);fake=copy.deepcopy(sample);lineage={k:{'value':None} for k in p.PLAYER};lineage['home_lineup_strength']['value']=[{'player_id':'wrong'}]
 try:p.validate_history_owners(fake,lineage)
 except ValueError:pass
 else:raise ValueError('ID mismatch not rejected')
 boundary=[{'season':2024,'event_date':'2024-12-24'},{'season':2024,'event_date':'2024-12-25'},{'season':2024,'event_date':'2024-12-26'}]
 need([z['event_date'] for z in p.original.cut_training(boundary,(2024,),'2025-01-01')]==['2024-12-24','2024-12-25'],'7-day boundary')
 raw=blob(REPO,DEV,P+'artifacts/development.json');need(sha(raw)=='e598cf380f03a8544559592cca8f055217bd4a24f1b58863b60866fa12fdec45','development hash');d=json.loads(raw)
 need(d['2026_labels_read'] is False and d['family_or_hyperparameter_selection'] is False and d['paired_source_commit']==SOURCE and d['training_n']==1451 and d['validation_n']==731,'development metadata')
 validation={s:[x for x in pairs[s] if x['season']==2025] for s in ('actual','projected')};cutoff=date.fromisoformat(validation['actual'][0]['event_date'])-timedelta(days=7);ncheck=0;errors=[];gradient={}
 for name,model in d['models'].items():
  selector=name.split('_')[1];family='M0' if name.startswith('M0') else 'M2';train=[x for x in pairs[selector] if x['season'] in (2023,2024) and date.fromisoformat(x['event_date'])<=cutoff]
  need(len(train)==model['n_train']==1451 and model['family']==('M0' if family=='M0' else 'M2_v2') and model['player_l2']==(10. if family=='M0' else 100.),'fixed fitting configuration '+name)
  independent=ref.train_spec([adapt(r) for r in train]);actual=model['spec'];need(all(independent[k]==actual[k] for k in ('teams','hitters','starters')) and np.allclose(independent['mean'],actual['mean'],rtol=1e-13,atol=1e-13) and np.allclose(independent['scale'],actual['scale'],rtol=1e-13,atol=1e-13),'training-only scaling/vocabulary '+name)
  X=np.concatenate([ref.design(adapt(r),actual,family) for r in train],axis=0);base=model['nbase'];beta=np.asarray(model['beta']);pen=np.r_[0.,np.full(base-1,10.),np.full(X.shape[1]-base,model['player_l2'])]
  if family=='M0':
   y=np.array([score for r in train for score in (labels[r['fixture_id']]['home_score'],labels[r['fixture_id']]['away_score'])]);z=X@beta;mu=np.exp(z);objective=np.sum(mu-y*z)+.5*np.sum(beta*beta*pen);grad=X.T@(mu-y)+beta*pen
  else:
   y=np.array([0 if labels[r['fixture_id']]['home_score']>labels[r['fixture_id']]['away_score'] else 1 if labels[r['fixture_id']]['home_score']==labels[r['fixture_id']]['away_score'] else 2 for r in train]);bb=beta.reshape(2,-1);logits=np.column_stack((X@bb.T,np.zeros(len(y))));m=logits.max(axis=1);logsum=m+np.log(np.exp(logits-m[:,None]).sum(axis=1));probs=np.exp(logits-logsum[:,None]);objective=np.sum(logsum-logits[np.arange(len(y)),y])+.5*np.sum(bb*bb*pen);grad=np.vstack([X.T@(probs[:,k]-(y==k))+bb[k]*pen for k in (0,1)])
  need(check_close(float(objective),model['objective'],1e-10),'objective/L2 mismatch '+name);gmax=float(np.max(np.abs(grad)));need(check_close(gmax,model['gradient_max'],1e-7),'gradient mismatch '+name);gradient[name]=gmax
  for scenario in ('actual','projected'):
   pred=d['predictions'][scenario][name];need(len(pred)==len(validation[scenario])==731,'prediction count')
   records=[]
   for row,frozen in zip(validation[scenario],pred):
    need(row['fixture_id']==frozen['fixture_id'],'prediction fixture');rr=adapt(row);indmodel={**model,'family':family};probs,_=ref.prediction(indmodel,rr);arr=[frozen['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(probs,arr));errors.append(err);need(err<1e-11,'independent prediction '+name+' '+scenario)
    label=labels[row['fixture_id']];winner='home' if label['home_score']>label['away_score'] else 'away' if label['home_score']<label['away_score'] else 'draw';records.append({'probabilities':dict(zip(('home','draw','away'),probs)),'winner':winner});ncheck+=1
   z=ref.outcome_metrics(records);m=d['results'][scenario]['metrics'][name];need(z['n']==m['n'] and z['draws']==m['draws'] and z['decisive_hits']==m['decisive_hits'],'outcome metrics '+name+' '+scenario)
 print(json.dumps({'code_commit':CODE,'development_commit':DEV,'development_sha256':sha(raw),'train_n':1451,'validation_n':731,'four_models':sorted(d['models']),'independently_checked_predictions':ncheck,'max_probability_error':max(errors),'gradient_max':gradient,'fixture_adapter_exact':True,'train_only_spec_exact':True,'final_fit_freeze_gate':'code verifies exact committed development blob before loading/fitting; execution order awaits final artifact','final_model_audited':False},sort_keys=True,indent=2))
if __name__=='__main__':main()
