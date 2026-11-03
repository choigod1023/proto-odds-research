"""Independently replay frozen 2026 predictions; does not read 2026 outcomes."""
import json,subprocess,sys
from datetime import date,timedelta
import numpy as np
from audit_development_frozen import REPO,SNAP,P,CODE,DEV,SOURCE,blob,sha,need,adapt,check_close
import reference as ref
FINAL='ff2bef35988eb5add154b4bebf8b265f42792db1'
def main():
 subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',DEV,FINAL])
 changed=subprocess.check_output(['git','-C',str(REPO),'diff','--name-only',CODE,FINAL]).decode().splitlines()
 need(set(changed)=={P+'artifacts/development.json',P+'artifacts/final-models.json',P+'artifacts/predictions.json'},'code changed after pin')
 sys.path.insert(0,str(SNAP));import dataset,pipeline as p
 pairs,labels,target,man=dataset.load();mb=blob(REPO,FINAL,P+'artifacts/final-models.json');pb=blob(REPO,FINAL,P+'artifacts/predictions.json')
 need(sha(mb)=='b3a1f2555d81776723a7d3bbc712aadc6f6a0a9371944fa31342facf6ef58fee' and sha(pb)=='b41b8dc71af26ccfb5b17ad568309dd4bf742df15e2ba8aaf5091eda693aabbd','frozen blob hash')
 models=json.loads(mb);pred=json.loads(pb);devsha='e598cf380f03a8544559592cca8f055217bd4a24f1b58863b60866fa12fdec45'
 for a in (models,pred):
  need(a['development_commit']==DEV and a['development_sha256']==devsha and a['paired_source_commit']==SOURCE and a['training_n']==2182 and a['target_n']==709 and a['2026_labels_read'] is False and a['historical_receipt_verified'] is False and a['no_model_or_hyperparameter_selection'] is True,'freeze lineage metadata')
 old0=json.loads((SNAP/'vendor/old-final-models.json').read_bytes())['models']['M0'];old2=json.loads((SNAP/'vendor/old-v2-model.json').read_bytes())['model'];need(models['models']['oldfull_M0']==old0 and models['models']['oldfull_M2']==old2,'baseline differs from frozen old-full model')
 targetfirst=min(x['event_date'] for x in target['actual']);cutoff=date.fromisoformat(targetfirst)-timedelta(days=7);errors=[];max_2way=0.;gradients={};count=0;flags_count=0
 for name,model in models['models'].items():
  fam='M0' if model['family']=='M0' else 'M2';need(model['family'] in ('M0','M2_v2'),'unknown family')
  if not name.startswith('oldfull_'):
   sel=name.split('_')[1];train=[x for x in pairs[sel] if x['season'] in (2023,2024,2025) and date.fromisoformat(x['event_date'])<=cutoff];need(len(train)==model['n_train']==2182 and model['player_l2']==(10 if fam=='M0' else 100),'final L2 or train count '+name)
   spec=ref.train_spec([adapt(x) for x in train]);old=model['spec'];need(all(spec[k]==old[k] for k in ('teams','hitters','starters')) and np.allclose(spec['mean'],old['mean'],rtol=1e-13,atol=1e-13) and np.allclose(spec['scale'],old['scale'],rtol=1e-13,atol=1e-13),'training-only spec '+name)
   X=np.concatenate([ref.design(adapt(r),old,fam) for r in train]);beta=np.asarray(model['beta']);base=model['nbase'];pen=np.r_[0.,np.full(base-1,10.),np.full(X.shape[1]-base,model['player_l2'])]
   if fam=='M0':
    y=np.array([s for r in train for s in (labels[r['fixture_id']]['home_score'],labels[r['fixture_id']]['away_score'])]);z=X@beta;mu=np.exp(z);objective=np.sum(mu-y*z)+.5*np.sum(beta*beta*pen);g=X.T@(mu-y)+beta*pen
   else:
    y=np.array([0 if labels[r['fixture_id']]['home_score']>labels[r['fixture_id']]['away_score'] else 1 if labels[r['fixture_id']]['home_score']==labels[r['fixture_id']]['away_score'] else 2 for r in train]);beta=beta.reshape(2,-1);logits=np.column_stack((X@beta.T,np.zeros(len(y))));m=logits.max(axis=1);ls=m+np.log(np.exp(logits-m[:,None]).sum(axis=1));pr=np.exp(logits-ls[:,None]);objective=np.sum(ls-logits[np.arange(len(y)),y])+.5*np.sum(beta*beta*pen);g=np.vstack([X.T@(pr[:,k]-(y==k))+beta[k]*pen for k in (0,1)])
   need(check_close(float(objective),model['objective'],1e-10) and check_close(float(np.max(np.abs(g))),model['gradient_max'],1e-7),'fit objective/gradient '+name);gradients[name]=float(np.max(np.abs(g)))
  for scenario in ('actual','projected'):
   out=pred['predictions'][scenario][name];rows=target[scenario];need(len(out)==len(rows)==709,'target count')
   for r,q in zip(rows,out):
    need(r['fixture_id']==q['fixture_id'] and r['event_date']==q['event_date'],'prediction fixture');one=adapt(r);prob,_=ref.prediction({**model,'family':fam},one);published=[q['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(prob,published));need(err<1e-11,'independent probability '+name+' '+scenario);errors.append(err)
    for i,side in ((0,'home'),(2,'away')):
     err2=abs(prob[i]/(prob[0]+prob[2])-q['probabilities_2way'][side]);need(err2<1e-11,'decisive conditional probability');max_2way=max(max_2way,err2)
    for side in ('home','away'):
     need(np.allclose(ref.flags(one,side,model['spec']),q['roster_coverage'][side],rtol=0,atol=1e-12),'roster quality flags');flags_count+=1
    count+=1
 print(json.dumps({'final_commit':FINAL,'models_sha256':sha(mb),'predictions_sha256':sha(pb),'development_ancestor':True,'training_cutoff':cutoff.isoformat(),'models':len(models['models']),'scenarios':2,'fixtures_per_scenario':709,'predictions_checked':count,'team_quality_flags_checked':flags_count,'max_probability_error':max(errors),'max_conditional_error':max_2way,'gradient_max':gradients,'prior_full_baselines_byte_content_equal':True,'2026_outcomes_read':False,'model_adoption':False},sort_keys=True,indent=2))
if __name__=='__main__':main()
