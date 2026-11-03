"""고정 공통 계약·입력과 이전 M0 재현을 독립 검토한다."""
import collections,gzip,hashlib,json,math,subprocess,sys
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent; ROOT=HERE.parent;sys.path.insert(0,str(ROOT/'common'));import core
import reference as ref
REPO=Path(r'C:\Users\user\Documents\Codex\kbtmodel');SOURCE=Path(r'C:\Users\user\Documents\Codex\kbtdata')
def git(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(raw):return hashlib.sha256(raw).hexdigest()
def need(v,msg):
 if not v:raise ValueError(msg)
def norm(row):return {'home_id':row['home_id'],'away_id':row['away_id'],'roster':row['roster'],'values':{k:v['value'] for k,v in row['features'].items()}}
def main():
 contract=json.loads((ROOT/'contract.json').read_bytes());manifest=core.verify();need(contract['maximum_nonbaseline_trials']==11 and sum(len(z['configs']) for z in contract['families'].values())==11,'후보 수')
 need(contract['validation_selection']==['maximum decisive two-way accuracy','minimum decisive two-way log loss','baseline preferred on complete ties','lexical configuration id'],'선택 우선순위')
 need(contract['outcome_access'].startswith('common materialization excludes2026labels and prices') and manifest['test_labels_or_prices_materialized'] is False,'결과 분리')
 pinned={'train-actual.jsonl.gz':'paired-train-actual.jsonl.gz','test-actual.jsonl.gz':'paired-test-actual.jsonl.gz','test-projected.jsonl.gz':'paired-test-projected.jsonl.gz'}
 for local,remote in pinned.items():
  b=(ROOT/'common/inputs'/local).read_bytes();orig=git(SOURCE,'58556de08c64604276de8682373d3ccc8d535d7c','experiments/kbo-actual-training-data-20261103/'+remote);need(b==orig,'원본 파일 불일치 '+local)
 data=core.load();train,val=core.development(data);end=core.final_training(data);need((len(train),len(val),len(end),len(data['test_actual']),len(data['test_projected']))==(1451,731,2182,709,709),'분할')
 counts=collections.Counter(x['assumed_completed_date'][:4] for x in data['labels'].values());need(dict(counts)=={'2023':723,'2024':728,'2025':731},'2026 레이블 포함 또는 분포 변경')
 baseline=core.baseline_reference();need(set(baseline)=={'development_model','development_predictions','final_model','final_predictions'},'기준 자료 구조')
 def no_outcomes(obj):
  if isinstance(obj,dict):
   need(not set(obj)&{'home_score','away_score','winner','outcome','odds','prices','price','net','roi','label','labels','result'},'기준 예측 안에 결과 또는 배당')
   for x in obj.values():no_outcomes(x)
  elif isinstance(obj,list):
   for x in obj:no_outcomes(x)
 no_outcomes(baseline)
 devraw=git(REPO,'0277fa14b2e7ee2f950d683cf0093f9f1b6d788d','experiments/kbo-actual-training-20261103/artifacts/development.json');need(sha(devraw)=='e598cf380f03a8544559592cca8f055217bd4a24f1b58863b60866fa12fdec45','기존 개발 산출물 SHA');d=json.loads(devraw)
 need(baseline['development_model']==d['models']['M0_actual'] and baseline['development_predictions']==d['predictions']['actual']['M0_actual'],'기준 개발 산출물 바이트 값')
 fm=json.loads(git(REPO,'ff2bef35988eb5add154b4bebf8b265f42792db1','experiments/kbo-actual-training-20261103/artifacts/final-models.json'));fp=json.loads(git(REPO,'ff2bef35988eb5add154b4bebf8b265f42792db1','experiments/kbo-actual-training-20261103/artifacts/predictions.json'));need(baseline['final_model']==fm['models']['M0_actual'] and baseline['final_predictions']==fp['predictions']['actual']['M0_actual'],'기준 최종 예측')
 model=core.fit_baseline(train,data['labels']);old=baseline['development_model'];need(model['family']=='M0' and model['player_l2']==10 and model['n_train']==1451,'M0 설정');need(model['spec']==old['spec'] and np.allclose(model['beta'],old['beta'],rtol=0,atol=1e-12) and abs(model['objective']-old['objective'])<1e-10,'M0 재학습 불일치')
 spec=ref.train_spec([norm(r) for r in train]);need(all(spec[k]==model['spec'][k] for k in ('teams','hitters','starters')) and np.allclose(spec['mean'],model['spec']['mean'],atol=1e-13) and np.allclose(spec['scale'],model['spec']['scale'],atol=1e-13),'학습 전용 사전/스케일')
 predictions=core.predict_baseline(model,val);maxerr=0.
 for r,p,gold in zip(val,predictions,baseline['development_predictions']):
  need(r['fixture_id']==p['fixture_id']==gold['fixture_id'],'검증 경기 ID');a=[p['probabilities_3way'][k] for k in ('home','draw','away')];b=[gold['probabilities_3way'][k] for k in ('home','draw','away')];q,_=ref.prediction(model,norm(r));maxerr=max(maxerr,*[abs(x-y) for x,y in zip(a,b)],*[abs(x-y) for x,y in zip(a,q)])
 need(maxerr<1e-11,'기준 확률 불일치')
 # 실제 2025 전체가 아니라 임의 부분만으로도 정상 선택이 되는지 확인한다.
 labels={'x':{'fixture_id':'x','assumed_completed_date':'2025-04-01','home_score':1,'away_score':0},'y':{'fixture_id':'y','assumed_completed_date':'2025-04-02','home_score':0,'away_score':1}}
 def p(fid,v):return {'fixture_id':fid,'probabilities_2way':{'home':v,'away':1-v},'probabilities_3way':{'home':v*.9,'draw':.1,'away':(1-v)*.9}}
 base=[p('x',.6),p('y',.6)];cand=[p('x',.4),p('y',.2)];full=core.choose({'baseline':base,'candidate':cand},labels)['selected'];partial=core.choose({'baseline':base[:1],'candidate':cand[:1]},labels)['selected'];need((full,partial)==('candidate','baseline'),'선택 반례가 재현되지 않음')
 tied=core.choose({'zz':base,'baseline':base,'aa':base},labels);need(tied['ranking']==['baseline','aa','zz'],'동률 순서')
 try:core.choose({'baseline':base,'candidate':cand[:1]},labels)
 except ValueError:pass
 else:raise ValueError('후보 간 서로 다른 경기 집합 수용')
 print(json.dumps({'상태':'공통선택함수_보강필요','계약SHA256':core.CONTRACT_SHA,'학습':len(train),'검증':len(val),'최종학습':len(end),'평가시나리오별':len(data['test_actual']),'M0재현_최대확률오차':maxerr,'2026결과배당_공통입력에없음':True,'부분집합반례':{'전체2경기선택':full,'부분1경기선택':partial,'함수에서부분집합거부':False},'후보고정산출물감사':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
