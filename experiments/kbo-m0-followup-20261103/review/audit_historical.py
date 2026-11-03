"""2023 전용 적합과 고정 2024 예측을 결과 접근 차단 상태에서 재생한다."""
import copy,hashlib,json,math,subprocess,sys,types
from pathlib import Path
import numpy as np
import reference as ref
ROOT=Path(__file__).resolve().parent.parent;REPO=Path(r'C:\Users\user\Documents\Codex\kbmhold24');BASE='experiments/kbo-m0-followup-20261103/historical/';IM='008668cfa429b3991ff9273b4c2a3df517206839';FIN='7f06e76dfe77cbb3dd7d9395995133effdb1b66a'
def git(rev,p):return subprocess.check_output(['git','-C',str(REPO),'show',rev+':'+BASE+p])
def sha(b):return hashlib.sha256(b).hexdigest()
def need(v,msg):
 if not v:raise ValueError(msg)
def row(r):return {'home_id':r['home_id'],'away_id':r['away_id'],'roster':r['roster'],'values':{k:v['value'] for k,v in r['features'].items()}}
def pmf(mu,alpha):
 n=1/alpha;z=alpha*mu/(1+alpha*mu);p=math.exp(-n*math.log1p(alpha*mu));r=[p];total=p
 for i in range(1,10000):
  p*=((i-1+n)/i)*z;r.append(p);total+=p
  if i>mu and (1-total<1e-14 or p<1e-17):break
 need(abs(total-1)<1e-12,'NB 꼬리');return r
def nb3(h,a):
 x=pmf(h,.3);y=pmf(a,.3);n=max(len(x),len(y));x += [0.]*(n-len(x));y += [0.]*(n-len(y));ch=ca=0.;out=[0.,0.,0.]
 for h,a in zip(x,y):out[0]+=h*ca;out[1]+=h*a;out[2]+=a*ch;ch+=h;ca+=a
 return [x/sum(out) for x in out]
def shrink_independent(r):
 out={k:copy.deepcopy(r[k]) for k in ('fixture_id','event_date','season','home_id','away_id','features','roster')}
 for side in ('home','away'):
  k=side+'_lineup_strength';src=r['raw_features'][k]['value']
  if src is not None and r['features'][k]['value'] is not None:
   h=pa=0
   for person in src:
    for z in person.get('history') or []:
     ab,hit,bb=z.get('ab'),z.get('hit'),z.get('bb')
     if all(type(x) is int and x>=0 for x in (ab,hit,bb)) and hit<=ab:h+=hit+bb;pa+=ab+bb
   out['features'][k]['value']=(h+6.6*len(src))/(pa+20*len(src))
  k=side+'_starter_strength';src=r['raw_features'][k]['value']
  if src is not None and r['features'][k]['value'] is not None:
   er=outs=0
   for z in src['appearances']:
    a,b=z.get('er'),z.get('outs')
    if all(type(x) is int and x>=0 for x in (a,b)):er+=a;outs+=b
   out['features'][k]['value']=27*(er+4.5)/(outs+27)
 return out
def main():
 subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',IM,FIN]);code=git(IM,'run.py');need(code==git(FIN,'run.py'),'실행 코드 변동');mb=git(FIN,'models2023.json');pb=git(FIN,'predictions2024.json');meta=json.loads(git(FIN,'manifest.json'));need(sha(mb)==meta['sha256']['models2023.json']=='6efad3c7f2ac7a966f65d377a6e1030d4ec957b3c8a7d8e9adcc0cb4c23f35dd' and sha(pb)==meta['sha256']['predictions2024.json']=='2643db9ee19e01de8a31ac13316ad0b2c05366ccf5ffb911b4b8b865a0d4f92b','산출물 해시');stored=json.loads(mb);pred=json.loads(pb)
 denied=[];seen=[];forbidden=('development-labels.jsonl.gz','baseline-reference.json','test-labels.jsonl.gz','price-ledgers.json','price-reject-audit.jsonl.gz','final-predictions.json.gz','final-models.json')
 def guard(event,args):
  if event=='open' and args and isinstance(args[0],(str,bytes,Path)):
   path=str(args[0]);seen.append(path)
   if any(name in path for name in forbidden):denied.append(path);raise PermissionError('2024/25 결과 또는 미래 학습 모델 접근 차단: '+path)
  if event=='subprocess.Popen':raise PermissionError('적합 도중 외부 프로세스 차단')
 sys.addaudithook(guard)
 # 감시가 실제로 금지 파일을 거부하는지 별도로 확인하고 시험 접근 기록은 분리한다.
 for denied_name in ('development-labels.jsonl.gz','baseline-reference.json'):
  try:(ROOT/'vendor/evolution/common/inputs'/denied_name).read_bytes()
  except PermissionError:pass
  else:raise ValueError('파일 접근 차단 미작동: '+denied_name)
 guard_tests=len(denied);need(guard_tests==2,'차단 시험 수');denied.clear()
 module=types.ModuleType('historical_review');module.__file__=str(ROOT/'historical/run.py');exec(compile(code,module.__file__,'exec'),module.__dict__);train,target,labels=module.inputs();need(len(train)==len(labels)==723 and len(target)==728 and all(x['season']==2023 for x in train) and all(x['season']==2024 for x in target),'훈련/평가 경계')
 # 실제 구현의 적합을 별도 디렉터리·프로세스에서 한 번 재실행하고 결과 접근을 감시한다.
 reproduced=module.fit_components(train,labels);need(not denied,'금지 경로 접근');errors=[]
 for name in ('baseline','shrink','nb03'):
  m=stored[name]['estimator'] if name=='shrink' else stored[name];again=reproduced[name]['estimator'] if name=='shrink' else reproduced[name];need(m['n_train']==again['n_train']==723 and m['spec']==again['spec'] and np.allclose(m['beta'],again['beta'],rtol=1e-10,atol=1e-10),'훈련 모델 재현 '+name)
  tr=[shrink_independent(r) if name=='shrink' else module.core.normalize(r) for r in train];va=[shrink_independent(r) if name=='shrink' else module.core.normalize(r) for r in target]
  if name=='shrink':
   for original,manual in zip(train+target,tr+va):need(module.shrink.transform(original,'shrink')==manual,'수축 원자료 변환')
  spec=ref.train_spec([row(r) for r in tr]);need(spec['teams']==m['spec']['teams'] and spec['hitters']==m['spec']['hitters'] and spec['starters']==m['spec']['starters'] and np.allclose(spec['mean'],m['spec']['mean'],rtol=0,atol=1e-13) and np.allclose(spec['scale'],m['spec']['scale'],rtol=0,atol=1e-13),'학습 전용 scaler '+name)
  X=np.concatenate([ref.design(row(r),m['spec'],'M0') for r in tr]);b=np.array(m['beta']);v=X@b;mu=np.exp(v);y=np.array([s for r in tr for s in (labels[r['fixture_id']]['home_score'],labels[r['fixture_id']]['away_score'])]);pen=np.r_[0.,np.full(len(b)-1,10.)]
  if name=='nb03':obj=np.sum((y+1/.3)*np.log1p(.3*mu)-y*v)+.5*np.sum(pen*b*b);grad=X.T@((mu-y)/(1+.3*mu))+pen*b
  else:obj=np.sum(mu-y*v)+.5*np.sum(pen*b*b);grad=X.T@(mu-y)+pen*b
  need(math.isclose(obj,m['objective'],rel_tol=1e-10) and math.isclose(max(abs(grad)),m['gradient_max'],rel_tol=1e-6,abs_tol=1e-7),'목적함수/기울기 '+name)
  for r,p in zip(va,pred[name]):
   need(r['fixture_id']==p['fixture_id'] and r['event_date']==p['event_date'],'예측 ID');rates=np.exp(ref.design(row(r),m['spec'],'M0')@b);prob=nb3(*rates) if name=='nb03' else ref.poisson(*rates);q=[p['probabilities_3way'][k] for k in ('home','draw','away')];err=max(abs(a-b) for a,b in zip(prob,q));need(err<1e-11,'독립 3방향 예측 '+name);errors.append(err)
 for i,r in enumerate(target):
  vv={k:sum(pred[n][i]['probabilities_3way'][k] for n in ('baseline','shrink','nb03'))/3 for k in ('home','draw','away')};p=pred['equal_ensemble'][i];den=vv['home']+vv['away'];need(p['fixture_id']==r['fixture_id'] and all(abs(p['probabilities_3way'][k]-vv[k])<1e-14 for k in vv) and all(abs(p['probabilities_2way'][k]-vv[k]/den)<1e-14 for k in ('home','away')),'1/3 앙상블')
 need(any('fit2023-labels.jsonl.gz' in x for x in seen) and not denied,'결과 접근 감시')
 print(json.dumps({'상태':'2023전용fit_및2024예측_검증통과','구현커밋':IM,'예측커밋':FIN,'모델SHA256':sha(mb),'예측SHA256':sha(pb),'학습':723,'평가':728,'독립예측':2184,'앙상블':728,'최대확률오차':max(errors),'차단장치_의도적시험':guard_tests,'실행중금지파일접근시도':len(denied),'실제2024결과열람':False,'범위':'Python 파일 접근 감시 및 코드 재실행; 외부 인증이 아닌 로컬 재현'},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
