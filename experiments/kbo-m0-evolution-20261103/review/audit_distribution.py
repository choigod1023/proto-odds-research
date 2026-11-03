"""음이항·무승부 진단의 고정 예측을 독립 수식으로 검증한다."""
import gzip,hashlib,json,math,subprocess,sys,types
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;sys.path.insert(0,str(ROOT/'common'));import core
import reference as ref
from audit_recency import same,rec,need
REPO=Path(r'C:\Users\user\Documents\Codex\kbmdist');P='experiments/kbo-m0-evolution-20261103/distribution/';IM='5db520b7fd89a0c4e28a0b45751bd563ce852d80';DV='373cd13a2590c2c0e8c1fb0516dbc9e5396e80a0';FIN='fddc7f998d4b1860867601529192031f1de188de'
def git(c,p):return subprocess.check_output(['git','-C',str(REPO),'show',c+':'+P+p])
def sha(x):return hashlib.sha256(x).hexdigest()
def pmf(mu,alpha):
 r=1/alpha;q=alpha*mu/(1+alpha*mu);v=math.exp(-r*math.log1p(alpha*mu));p=[v];total=v
 for k in range(1,10000):
  v*=((k-1+r)/k)*q;p.append(v);total+=v
  if k>mu and (1-total<1e-14 or v<1e-17):break
 need(abs(1-total)<1e-12,'NB 꼬리 확률');return p
def nb3(h,a,alpha):
 h=pmf(h,alpha);a=pmf(a,alpha);n=max(len(h),len(a));h+=([0.]*(n-len(h)));a+=([0.]*(n-len(a)));ph=pa=0.;res=[0.,0.,0.]
 for x,y in zip(h,a):res[0]+=x*pa;res[1]+=x*y;res[2]+=y*ph;ph+=x;pa+=y
 return [x/sum(res) for x in res]
def probabilities(p):return [p['probabilities_3way'][k] for k in ('home','draw','away')]
def rates(model,rows):
 X=np.concatenate([ref.design(rec(r),model['spec'],'M0') for r in rows]);return X,np.exp(X@np.asarray(model['beta'])).reshape(-1,2)
def nb_model_check(model,rows,labels):
 spec=ref.train_spec([rec(x) for x in rows]);same(spec,model['spec'],'훈련전용 스케일/사전');need(model['n_train']==len(rows) and model['family']=='nb2','NB 학습 수')
 X,mu0=rates(model,rows);mu=mu0.ravel();y=np.array([s for r in rows for s in (labels[r['fixture_id']]['home_score'],labels[r['fixture_id']]['away_score'])]);beta=np.array(model['beta']);alpha=model['alpha'];pen=np.r_[0.,np.full(len(beta)-1,10.)];z=X@beta;loss=float(np.sum((y+1/alpha)*np.log1p(alpha*mu)-y*z)+.5*np.sum(pen*beta**2));grad=X.T@((mu-y)/(1+alpha*mu))+pen*beta
 need(math.isclose(loss,model['objective'],rel_tol=1e-10) and math.isclose(float(max(abs(grad))),model['gradient_max'],rel_tol=1e-6,abs_tol=1e-7),'NB 목적함수 또는 기울기')
 def f(b):
  z=X@b;m=np.exp(z);return float(np.sum((y+1/alpha)*np.log1p(alpha*m)-y*z)+.5*np.sum(pen*b*b))
 for k in (0,1,len(beta)-1):
  h=1e-5;e=np.zeros(len(beta));e[k]=h;numerical=(f(beta+e)-f(beta-e))/(2*h);need(abs(numerical-grad[k])<1e-5,'NB 수치 미분')
 return model

def check_preds(model,rows,pred,kind):
 need(len(rows)==len(pred),'예측 수');errs=[]
 if kind=='nb':
  X,rr=rates(model,rows)
  for row,p,(h,a) in zip(rows,pred,rr):
   need(row['fixture_id']==p['fixture_id'],'예측 ID');q=nb3(h,a,model['alpha']);v=probabilities(p);err=max(abs(x-y) for x,y in zip(q,v));need(err<1e-11,'NB 독립 확률');need(p['tail_bound']<=1.01e-13,'보고된 꼬리 상한');errs.append(err)
 else:
  base=model['baseline'];offset=model['draw_intercept']
  for row,p in zip(rows,pred):
   need(row['fixture_id']==p['fixture_id'],'무승부 진단 ID');q,_=ref.prediction(base,rec(row));d0=q[1];d=1/(1+math.exp(-(math.log(d0/(1-d0))+offset)));h=q[0]/(q[0]+q[2]);v=[(1-d)*h,d,(1-d)*(1-h)];err=max(abs(x-y) for x,y in zip(v,probabilities(p)));need(err<1e-11 and abs(h-p['probabilities_2way']['home'])<1e-12,'무승부 진단/승패 방향 변경');errs.append(err)
 return max(errs)
def hurdle_train(model,train,labels):
 baseline=model['baseline'];need(baseline['n_train']==len(train),'기준 학습 수');draws=sum(labels[x['fixture_id']]['home_score']==labels[x['fixture_id']]['away_score'] for x in train);target=(draws+.5)/(len(train)+1);need(model['training_draws']==draws and math.isclose(model['smoothed_draw_target'],target,abs_tol=1e-15),'훈련 무승부 target')
 ds=[]
 for x in train:
  q,_=ref.prediction(baseline,rec(x));d=q[1];ds.append(1/(1+math.exp(-(math.log(d/(1-d))+model['draw_intercept']))))
 need(math.isclose(sum(ds)/len(ds),target,abs_tol=1e-12) and math.isclose(model['training_draw_mean_after_calibration'],target,abs_tol=1e-12),'훈련 절편 보정')
def main():
 for a,b in ((IM,DV),(DV,FIN)):subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',a,b])
 code=git(IM,'candidate.py');need(code==git(DV,'candidate.py')==git(FIN,'candidate.py'),'후보 코드 변동');mod=types.ModuleType('review_distribution');mod.__file__=str(ROOT/'distribution/candidate.py');exec(compile(code,mod.__file__,'exec'),mod.__dict__)
 data=core.load();train,val=core.development(data);finaltrain=core.final_training(data);db=git(DV,'artifacts/development.json.gz');fb=git(FIN,'artifacts/final-predictions.json.gz');d=json.loads(gzip.decompress(db));f=json.loads(gzip.decompress(fb));need(f['development_sha256']==sha(db) and f['candidate_code_sha256']==sha(code),'개발과 코드 해시')
 need(d['train_count']==1451 and d['validation_count']==731 and d['test_labels_or_prices_accessed'] is False and f['training_count']==2182 and f['test_labels_or_prices_accessed'] is False,'시점 분리')
 choice=core.choose(d['predictions'],data['labels']);same(choice,d['selection'],'새 공통 검증의 개발 선택');need(choice['selected']==f['selected']=='negative_binomial_alpha03','선택 설정');errors=[]
 for config,alpha in (('negative_binomial_alpha01',.1),('negative_binomial_alpha03',.3)):
  model=d['models'][config];need(model['alpha']==alpha,'알파 설정');nb_model_check(model,train,data['labels']);errors.append(check_preds(model,val,d['predictions'][config],'nb'))
 h=d['models']['draw_hurdle'];hurdle_train(h,train,data['labels']);errors.append(check_preds(h,val,d['predictions']['draw_hurdle'],'hurdle'))
 selected=f['selected_model'];need(selected['alpha']==.3 and selected['n_train']==2182,'최종 NB 선택');nb_model_check(selected,finaltrain,data['labels']);h=f['draw_hurdle_diagnostic_model'];hurdle_train(h,finaltrain,data['labels'])
 for side in ('actual','projected'):
  rr=data['test_'+side];p=f['selected_predictions']['test_'+side];hp=f['draw_hurdle_diagnostic_predictions']['test_'+side];need(len(rr)==len(p)==len(hp)==709,'최종 수');errors.append(check_preds(selected,rr,p,'nb'));errors.append(check_preds(h,rr,hp,'hurdle'))
  fake={r['fixture_id']:{'home_score':1,'away_score':0} for r in rr};core.metrics(p,fake);core.metrics(hp,fake)
 print(json.dumps({'상태':'분포후보_고정산출물_감사통과','구현커밋':IM,'개발커밋':DV,'최종커밋':FIN,'개발SHA256':sha(db),'최종SHA256':sha(fb),'선택':choice['selected'],'선택순서':choice['ranking'],'NB_개발예측검사':1462,'무승부진단_개발예측검사':731,'최종_예측검사':2836,'최대확률오차':max(errors),'수치미분_통과':True,'훈련전용무승부기준_통과':True,'2026결과열람':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
