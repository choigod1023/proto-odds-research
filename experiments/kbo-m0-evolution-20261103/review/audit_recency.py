"""고정 recency 후보의 독립 수식, 선택, 모형과 예측을 검사한다."""
import collections,copy,gzip,hashlib,json,math,subprocess,sys,types
from datetime import date
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;sys.path.insert(0,str(ROOT/'common'));import core
import reference as ref
REPO=Path(r'C:\Users\user\Documents\Codex\kbmrec');P='experiments/kbo-m0-evolution-20261103/recency/';IMPL='ff94372a35ed2a8945e2ef7cc042ca8a7ca09c34';DEV='7cc6b0b4aefcc52fcf2d7416d685ffdb33da6e2d';FINAL='1110b13e7a1b552607cbeed53468cf15ca3bfbda'
def git(commit,path):return subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+P+path])
def need(x,msg):
 if not x:raise ValueError(msg)
def sha(b):return hashlib.sha256(b).hexdigest()
def rec(row):return {'home_id':row['home_id'],'away_id':row['away_id'],'roster':row['roster'],'values':{k:v['value'] for k,v in row['features'].items()}}
def valid(*args):return all(type(x) is int and x>=0 for x in args)
def independently_transform(row,config):
 out=core.normalize(row);date0=date.fromisoformat(row['event_date']);hl={'decay30':30,'decay90':90,'shrink':None,'ordered_decay30_shrink':30}[config];shrink=config in ('shrink','ordered_decay30_shrink');ordered=config=='ordered_decay30_shrink'
 def w(x):
  days=(date0-date.fromisoformat(x['date'])).days;need(days>0,'당일 또는 미래 이력');return 1. if hl is None else 2**(-days/hl)
 for side in ('home','away'):
  n=side+'_lineup_strength';source=row['raw_features'][n]['value']
  if source is not None and row['features'][n]['value'] is not None:
   profiles=[]
   for p in source:
    h=pa=0.
    for z in p['history'] or []:
     ab,hit,bb=z.get('ab'),z.get('hit'),z.get('bb')
     if valid(ab,hit,bb) and hit<=ab:factor=w(z);h+=(hit+bb)*factor;pa+=(ab+bb)*factor
    profiles.append((h,pa))
   if ordered:
    need(len(profiles)==9,'타순 길이');val=sum(((h+6.6)/(pa+20.))*weight for (h,pa),weight in zip(profiles,(4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.,3.9)))/4.3/9
   else:
    h=sum(t[0] for t in profiles);pa=sum(t[1] for t in profiles)
    if shrink:h+=6.6*len(profiles);pa+=20.*len(profiles)
    val=h/pa if pa else None
   out['features'][n]['value']=val
  n=side+'_starter_strength';source=row['raw_features'][n]['value']
  if source is not None and row['features'][n]['value'] is not None:
   er=outs=0.
   for z in source['appearances']:
    if valid(z.get('er'),z.get('outs')):factor=w(z);er+=z['er']*factor;outs+=z['outs']*factor
   if shrink:er+=4.5;outs+=27.
   out['features'][n]['value']=27*er/outs if outs else None
 return out

def same(a,b,what):
 if isinstance(a,dict) and isinstance(b,dict):
  need(set(a)==set(b),what+' 키');
  for k in a:same(a[k],b[k],what+'/'+k)
 elif isinstance(a,list) and isinstance(b,list):
  need(len(a)==len(b),what+' 길이')
  for i,(x,y) in enumerate(zip(a,b)):same(x,y,what+'/'+str(i))
 elif isinstance(a,(float,int)) and not isinstance(a,bool) and isinstance(b,(float,int)) and not isinstance(b,bool):need(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12),what+' 값')
 else:need(a==b,what+' 값')
def main():
 for a,b in ((IMPL,DEV),(DEV,FINAL)):subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',a,b])
 code=git(IMPL,'candidate.py');need(code==git(DEV,'candidate.py')==git(FINAL,'candidate.py'),'후보 코드 변동')
 mod=types.ModuleType('review_recency_candidate');mod.__file__=str(ROOT/'recency/candidate.py');exec(compile(code,mod.__file__,'exec'),mod.__dict__)
 data=core.load();train,val=core.development(data);finaltrain=core.final_training(data);selraw=git(DEV,'development/selection.json');selection=json.loads(selraw);need(selection['contract_sha256']==core.CONTRACT_SHA and selection['used2026_outcomes'] is False,'개발 계약')
 configs=['decay30','decay90','shrink','ordered_decay30_shrink'];need(set(mod.CONFIGS)==set(configs) and selection['configurations']==mod.CONFIGS,'사전 후보 변경')
 predictions={'baseline':core.baseline_reference()['development_predictions']};models={};counts=collections.Counter();errors=[]
 for config in configs:
  model=json.loads(git(DEV,'development/'+config+'/model.json'));pp=json.loads(git(DEV,'development/'+config+'/predictions.json'));models[config]=model;predictions[config]=pp;need(model['config_id']==config and model['config']==mod.CONFIGS[config] and len(pp)==731,'개발 후보 구조')
  transformed=[]
  for r in train+val:
   a=mod.transform(r,config);b=independently_transform(r,config);same(a,b,'특성 '+config+' '+r['fixture_id']);counts['개발_후보별_변환']+=1
   if r['season']!=2025:transformed.append(a)
  est=model['estimator'];need(est['family']=='M0' and est['player_l2']==10 and est['n_train']==1451,'개발 fit 설정')
  spec=ref.train_spec([rec(x) for x in transformed]);same(spec,est['spec'],'개발 학습 전용 scaler/사전 '+config)
  X=np.concatenate([ref.design(rec(r),est['spec'],'M0') for r in transformed]);y=np.array([v for r in transformed for v in (data['labels'][r['fixture_id']]['home_score'],data['labels'][r['fixture_id']]['away_score'])]);b=np.asarray(est['beta']);z=X@b;pen=np.r_[0.,np.full(len(b)-1,10.)];mu=np.exp(z);obj=float(np.sum(mu-y*z)+.5*np.sum(b*b*pen));grad=float(np.max(np.abs(X.T@(mu-y)+b*pen)));need(math.isclose(obj,est['objective'],rel_tol=1e-10,abs_tol=1e-10) and math.isclose(grad,est['gradient_max'],rel_tol=1e-6,abs_tol=1e-7),'개발 목적/기울기 '+config)
  for r,p in zip(val,pp):
   need(r['fixture_id']==p['fixture_id'],'개발 예측 순서');q,_=ref.prediction(est,rec(independently_transform(r,config)));a=[p['probabilities_3way'][x] for x in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(q,a));need(err<1e-11,'개발 독립 예측');errors.append(err);counts['개발_예측']+=1
 # New fixed selection rejects invalid and incomplete candidates, even though worker used older core.
 choice=core.choose(predictions,data['labels']);need(choice['selected']==selection['selected'] and choice['ranking']==selection['ranking'],'731 고정 선택 불일치');same(choice['metrics'],selection['metrics'],'선택 지표')
 try:core.choose({**predictions,'shrink':predictions['shrink'][:-1]},data['labels'])
 except ValueError:pass
 else:raise ValueError('부분 예측 허용')
 manifest=json.loads(git(FINAL,'final/manifest.json'));need(manifest['development_commit']==DEV and manifest['test_outcomes_read'] is False and manifest['train_count']==2182 and manifest['selected']==choice['selected'],'개발-최종 동결 이력')
 rawfiles={n:git(FINAL,'final/'+n) for n in ('model.json','actual.json','projected.json')}
 for n,b in rawfiles.items():need(sha(b)==manifest['sha256'][n],'최종 산출물 해시 '+n)
 model=json.loads(rawfiles['model.json']);config=choice['selected'];need(model['config_id']==config and model['config']==mod.CONFIGS[config],'최종 선택 설정');est=model['estimator'];need(est['n_train']==2182 and est['player_l2']==10.,'최종 학습 수와 L2')
 transformed=[]
 for r in finaltrain:
  a=mod.transform(r,config);same(a,independently_transform(r,config),'최종 학습 변환');transformed.append(a);counts['최종_학습_변환']+=1
 spec=ref.train_spec([rec(r) for r in transformed]);same(spec,est['spec'],'최종 학습 전용 scaler/사전')
 X=np.concatenate([ref.design(rec(r),est['spec'],'M0') for r in transformed]);y=np.array([v for r in transformed for v in (data['labels'][r['fixture_id']]['home_score'],data['labels'][r['fixture_id']]['away_score'])]);b=np.asarray(est['beta']);z=X@b;mu=np.exp(z);pen=np.r_[0.,np.full(len(b)-1,10.)];obj=float(np.sum(mu-y*z)+.5*np.sum(b*b*pen));grad=float(np.max(np.abs(X.T@(mu-y)+b*pen)));need(math.isclose(obj,est['objective'],rel_tol=1e-10,abs_tol=1e-10) and math.isclose(grad,est['gradient_max'],rel_tol=1e-6,abs_tol=1e-7),'최종 목적/기울기')
 for s in ('actual','projected'):
  pp=json.loads(rawfiles[s+'.json']);rr=data['test_'+s];need(len(pp)==len(rr)==709,'최종 수');
  for r,p in zip(rr,pp):
   need(r['fixture_id']==p['fixture_id'] and r['event_date']==p['event_date'],'최종 경기 ID');a=mod.transform(r,config);b=independently_transform(r,config);same(a,b,'최종 예측 특성');q,_=ref.prediction(est,rec(b));v=[p['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(x-y) for x,y in zip(q,v));need(err<1e-11,'최종 독립 예측');errors.append(err);counts['최종_예측']+=1
  core.metrics(pp,{r['fixture_id']:{'home_score':1,'away_score':0} for r in rr}) # 합성 정답만 사용해 확률 형식 검사
 # 명시적 prior와 관측값 0, 미관측 null을 구분한다.
 sample=copy.deepcopy(train[-1]);day=sample['event_date'];n='home_lineup_strength';profiles=[{'player_id':str(i),'history':None} for i in range(9)];sample['raw_features'][n]['value']=profiles;sample['features'][n]['value']=None;need(mod.transform(sample,'shrink')['features'][n]['value'] is None,'전체 미관측 prior 대체')
 profiles[0]['history']=[{'date':'2023-01-01','ab':10,'hit':0,'bb':0}];sample['features'][n]['value']=0.;need(math.isclose(mod.transform(sample,'shrink')['features'][n]['value'],59.4/190),'관측 0 타격 prior 계산')
 n='home_starter_strength';sample['raw_features'][n]['value']={'projected_player_id':'p','appearances':[{'date':'2023-01-01','er':0,'outs':27,'source_bf_raw':99999}]};sample['features'][n]['value']=0.;need(math.isclose(mod.transform(sample,'shrink')['features'][n]['value'],2.25),'0실점 또는 BF 처리')
 sample['raw_features'][n]['value']['appearances'][0]['date']=day
 try:mod.transform(sample,'shrink')
 except ValueError:pass
 else:raise ValueError('당일 이력 허용')
 print(json.dumps({'상태':'recency_고정예측_감사통과','구현커밋':IMPL,'개발커밋':DEV,'최종커밋':FINAL,'코드_SHA256':sha(code),'선택':config,'선택순서':choice['ranking'],'개발731_새검증통과':True,'최종2182_목적함수_기울기일치':True,'최대확률오차':max(errors),'검사수':counts,'최종해시':manifest['sha256'],'2026결과열람':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
