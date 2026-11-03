"""상대 상호작용의 고정 개발 산출물을 독립 재생한다."""
import hashlib,json,math,subprocess,sys,types
from datetime import date
from pathlib import Path
import numpy as np
H=Path(__file__).resolve().parent;ROOT=H.parent;sys.path.insert(0,str(ROOT/'common'));import core
import reference as ref
REPO=Path(r'C:\Users\user\Documents\Codex\kbmopp');P='experiments/kbo-m0-evolution-20261103/opponent/';IM='a979dc46934dfc53eb86b314b9b0156c69b981a9';DV='ca41b6da7f6d5cb89fd5d5e8dbfb6ad882bfcafb';FIN='8c708d0915f08b2f17c51e34b266cafc574b1035'
def git(c,p):return subprocess.check_output(['git','-C',str(REPO),'show',c+':'+P+p])
def sha(x):return hashlib.sha256(x).hexdigest()
def need(v,msg):
 if not v:raise ValueError(msg)
def adapt(r):return {'home_id':r['home_id'],'away_id':r['away_id'],'roster':r['roster'],'values':{k:v['value'] for k,v in r['features'].items()}}
def matrix(rows,spec,interaction):
 res=[]
 for r in rows:
  row=adapt(r);base=ref.design(row,spec,'M0')
  if interaction:
   nums=[]
   for side in ('home','away'):
    z=[ref.numeric(v,m,s)[0] for v,m,s in zip(ref.oriented(row,side),spec['mean'],spec['scale'])];nums.append([z[5]*z[6],z[6]*z[7],z[2]*z[7]])
   base=np.column_stack((base,np.array(nums)))
  res.append(base)
 return np.concatenate(res)
def main():
 for a,b in ((IM,DV),(DV,FIN)):subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',a,b])
 code=git(IM,'candidate.py');need(code==git(DV,'candidate.py')==git(FIN,'candidate.py'),'후보 코드 변동');mod=types.ModuleType('review_opponent');mod.__file__=str(ROOT/'opponent/candidate.py');exec(compile(code,mod.__file__,'exec'),mod.__dict__)
 data=core.load();train,val=core.development(data);models=json.loads(git(DV,'development/models.json'));pred=json.loads(git(DV,'development/predictions.json'));selection=json.loads(git(DV,'development/selection.json'));need(set(models)==set(mod.CONFIGS),'후보 수')
 changed=core.choose(pred,data['labels']);need(changed['selected']==selection['selected']=='baseline' and changed['ranking']==selection['ranking'],'새 가드 기준 선택');need(len(pred['baseline'])==731,'선택 표본')
 scores=np.array([s for r in train for s in (data['labels'][r['fixture_id']]['home_score'],data['labels'][r['fixture_id']]['away_score'])]);maxerr=0.;last=max(date.fromisoformat(r['event_date']) for r in train)
 for name,model in models.items():
  cfg=mod.CONFIGS[name];spec=model['spec'];ind=ref.train_spec([adapt(r) for r in train]);need(spec['teams']==ind['teams'] and np.allclose(spec['mean'],ind['mean'],rtol=0,atol=1e-13) and np.allclose(spec['scale'],ind['scale'],rtol=0,atol=1e-13),'훈련 전용 표준화 '+name)
  expected_w=np.array([2**(-(last-date.fromisoformat(r['event_date'])).days/365) for r in train]) if cfg['decay_days'] else np.ones(len(train));expected_w/=expected_w.mean();got,anchor=mod.training_weights(train,cfg['decay_days']);need(anchor==model['weight_anchor_training_date']==last.isoformat() and np.allclose(got,expected_w,rtol=1e-13,atol=1e-13) and math.isclose(sum(got),1451,rel_tol=1e-12),'학습 가중치 '+name)
  X=matrix(train,spec,cfg['interaction_penalty'] is not None);actual,_=mod.design(train,spec,cfg);need(np.allclose(X,actual.toarray(),rtol=0,atol=1e-12),'상호작용 설계 '+name)
  beta=np.array(model['beta']);pen=np.r_[0.,np.full(model['nbase']-1,10.),np.full(3,cfg['interaction_penalty']) if cfg['interaction_penalty'] is not None else []];need(np.allclose(pen,model['penalties']),'벌점 '+name);z=X@beta;mu=np.exp(z);w=np.repeat(expected_w,2);objective=np.sum(w*(mu-scores*z))+.5*np.sum(pen*beta*beta);grad=np.max(np.abs(X.T@(w*(mu-scores))+pen*beta));need(math.isclose(objective,model['objective'],rel_tol=1e-10) and math.isclose(grad,model['gradient_max'],rel_tol=1e-6,abs_tol=1e-7),'목적함수 '+name)
  pv=pred[name];need(len(pv)==731,'예측 수');Y=matrix(val,spec,cfg['interaction_penalty'] is not None);rates=np.exp(Y@beta).reshape(-1,2)
  for row,p,rates_i in zip(val,pv,rates):
   need(row['fixture_id']==p['fixture_id'],'개발 ID');q=ref.poisson(*rates_i);v=[p['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(q,v));need(err<1e-11,'개발 예측');maxerr=max(maxerr,err)
 finalmodel=json.loads(git(FIN,'final/model.json'));f=json.loads(git(FIN,'final/predictions.json'));need(finalmodel['selected']==f['selected']=='baseline' and finalmodel['training_n']==2182 and f['used2026_outcomes'] is False,'최종 선택');need(finalmodel['model']==core.baseline_reference()['final_model'],'최종 기준 모델')
 for side in ('actual','projected'):
  pp=f[side];rows=data['test_'+side];need(len(pp)==len(rows)==709,'최종 표본');core.metrics(pp,{r['fixture_id']:{'home_score':1,'away_score':0} for r in rows})
  for r,p in zip(rows,pp):
   need(r['fixture_id']==p['fixture_id'],'최종 ID');q,_=ref.prediction(finalmodel['model'],adapt(r));v=[p['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(q,v));need(err<1e-11,'최종 기준 예측');maxerr=max(maxerr,err)
 print(json.dumps({'상태':'상대후보_고정산출물_감사통과','개발커밋':DV,'최종커밋':FIN,'코드_SHA256':sha(code),'선택':changed['selected'],'학습가중치_기준일':last.isoformat(),'개발예측검사':2924,'최종예측검사':1418,'최대확률오차':maxerr,'2026결과열람':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
