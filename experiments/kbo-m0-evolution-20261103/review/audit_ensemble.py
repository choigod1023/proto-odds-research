"""고정된 전역 선택과 세 후보의 동등 확률 평균을 독립 대조한다."""
import collections,gzip,hashlib,json,math,subprocess,sys
from pathlib import Path
H=Path(__file__).resolve().parent;ROOT=H.parent;sys.path.insert(0,str(ROOT/'common'));import core
import reference as ref
from audit_recency import same,need
REPO=Path(r'C:\Users\user\Documents\Codex\kbm0plus');BASE='experiments/kbo-m0-evolution-20261103/';COMMIT='627cadca9096acae18a30f72484c342edab7acd3'
def git(rev,path):return subprocess.check_output(['git','-C',str(REPO),'show',rev+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def index(rows):
 out={r['fixture_id']:r for r in rows};need(len(out)==len(rows),'중복 경기');return out
def metrics(rows,labels):
 records=[]
 for p in rows:
  y=labels[p['fixture_id']];result='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';records.append({'probabilities':p['probabilities_3way'],'winner':result})
 return ref.outcome_metrics(records)
def average(rows):
 v={k:math.fsum(r['probabilities_3way'][k] for r in rows)/3 for k in ('home','draw','away')};den=v['home']+v['away'];return v,{'home':v['home']/den,'away':v['away']/den}
def no_labels_or_prices(x):
 if isinstance(x,dict):
  need(not (set(x)&{'home_score','away_score','winner','outcome','price','prices','odds','net','roi','labels'}),'동결 예측에 결과 또는 가격 포함')
  for v in x.values():no_labels_or_prices(v)
 elif isinstance(x,list):
  for v in x:no_labels_or_prices(v)
def main():
 sb=git(COMMIT,BASE+'artifacts/selection.json');pb=git(COMMIT,BASE+'artifacts/predictions.json');need(sha(sb)=='ec6fc195fcb8dffde75a73f00e482332048de395e2665327c1af1c2ab15c832a' and sha(pb)=='8749562073997c4e2374effe3b53fa41efcd2e4e1a60f38ab658496633e732b4','통합 Git blob SHA')
 sel=json.loads(sb);out=json.loads(pb);need(sel['test_outcomes_loaded'] is False and out['test_outcomes_loaded'] is False and out['candidate_choice_used2026'] is False and out['contract_sha256']==core.CONTRACT_SHA,'결과 차단 또는 계약')
 need(sel['global_primary_winner_unchangeable_after_test'] is True and sel['source_hashes']==out['source_hashes'],'동결 메타데이터');no_labels_or_prices(out['predictions'])
 for key,h in sel['source_hashes'].items():
  rev,path=key.split(':',1);need(sha(git(rev,path))==h,'후보 원본 해시 '+path)
 # 731개 고정 검증 경기와 후보 원본·전역 선택을 독립 대조한다.
 data=core.load();_,val=core.development(data);valid={r['fixture_id'] for r in val};need(len(valid)==731,'검증 모집단');dev=sel['baseline_and_nominee_validation_predictions'];need(set(dev)=={'baseline','recency','opponent','distribution'},'전역 선택 후보')
 rdev='7cc6b0b4aefcc52fcf2d7416d685ffdb33da6e2d';odev='ca41b6da7f6d5cb89fd5d5e8dbfb6ad882bfcafb';ddev='373cd13a2590c2c0e8c1fb0516dbc9e5396e80a0'
 read=lambda rev,path:json.loads(gzip.decompress(b) if path.endswith('.gz') else b) if (b:=git(rev,BASE+path)) else None
 dsource=read(ddev,'distribution/artifacts/development.json.gz');osource=read(odev,'opponent/development/predictions.json');rsource=read(rdev,'recency/development/shrink/predictions.json')
 need(dev['baseline']==core.baseline_reference()['development_predictions'] and dev['recency']==rsource and dev['opponent']==osource['baseline'] and dev['distribution']==dsource['predictions']['negative_binomial_alpha03'],'개발 선택 후보 원본')
 independent={};rank=[]
 for name,rows in dev.items():
  need(set(index(rows))==valid,'전역 선택 표본 '+name);m=metrics(rows,data['labels']);stored=sel['global_selection']['metrics'][name]
  for k,own in [('n','n'),('hits','hits'),('accuracy','accuracy'),('logloss','logloss'),('brier','brier'),('decisive','decisive'),('decisive_hits','decisive_hits'),('decisive_accuracy','conditional_accuracy'),('decisive_logloss','conditional_logloss'),('decisive_brier','conditional_brier')]:need(math.isclose(stored[k],m[own],rel_tol=1e-12,abs_tol=1e-12),'전역 선택 지표 '+name+' '+k)
  independent[name]=m;rank.append((-m['conditional_accuracy'],m['conditional_logloss'],name!='baseline',name))
 ranking=[z[-1] for z in sorted(rank)];need(ranking==sel['global_selection']['ranking'] and ranking[0]==sel['global_selection']['selected']==out['primary']=='recency','전역 후보 순위')
 # 실제 2026 결과 대신 서로 반대인 합성 레이블을 주입해 선택 불변성을 확인한다.
 for hs,aw in ((100,0),(0,100)):
  spoof=dict(data['labels']);spoof.update({r['fixture_id']:{'assumed_completed_date':r['event_date'],'home_score':hs,'away_score':aw} for r in data['test_actual']})
  recalculated=core.choose(dev,spoof);need(recalculated==sel['global_selection'],'합성 2026 결과에 전역 선택 변경')
 need('equal_ensemble' not in sel['global_selection']['metrics'],'고정 앙상블을 선택 후보로 사용');devmaps={k:index(v) for k,v in dev.items()};ev=[]
 for r in val:
  q,t=average([devmaps[k][r['fixture_id']] for k in ('recency','opponent','distribution')]);ev.append({'fixture_id':r['fixture_id'],'probabilities_3way':q,'probabilities_2way':t})
 em=metrics(ev,data['labels']);stored=sel['equal_ensemble_validation_metrics'];need(em['n']==stored['n'] and em['decisive_hits']==stored['decisive_hits'] and math.isclose(em['conditional_logloss'],stored['decisive_logloss'],abs_tol=1e-12),'검증 앙상블')
 rfin='1110b13e7a1b552607cbeed53468cf15ca3bfbda';ofin='8c708d0915f08b2f17c51e34b266cafc574b1035';dfin='fddc7f998d4b1860867601529192031f1de188de';dist=read(dfin,'distribution/artifacts/final-predictions.json.gz');opp=read(ofin,'opponent/final/predictions.json');base=read(COMMIT,'artifacts/baseline-final.json');need(base['test_labels_read'] is False and base['actual_reference_predictions_exact'] is True,'기준 결과 상태')
 need(base['model']==core.baseline_reference()['final_model'] and base['prediction_sets']['actual']==core.baseline_reference()['final_predictions'],'기준 모델 출처')
 checked=0;errs=[];sources=collections.Counter()
 for side in ('actual','projected'):
  rows=out['predictions'][side];expected={'baseline':base['prediction_sets'][side],'recency':read(rfin,'recency/final/'+side+'.json'),'opponent':opp[side],'distribution':dist['selected_predictions']['test_'+side],'draw_hurdle':dist['draw_hurdle_diagnostic_predictions']['test_'+side]}
  need(set(rows)==set(expected)|{'equal_ensemble'},'여섯 예측 집합');target=index(data['test_'+side]);need(len(target)==709,'2026 경기 수')
  for name,preds in expected.items():
   need(preds==rows[name] and set(index(preds))==set(target),'후보 원본 또는 모집단 '+side+' '+name);sources[name]+=len(preds)
  need(rows['opponent']==rows['baseline'],'baseline 선택 재생')
  need(all(x['probabilities_2way']==y['probabilities_2way'] for x,y in zip(rows['draw_hurdle'],rows['baseline'])),'무승부 진단 승패 조건부 변경')
  maps={k:index(rows[k]) for k in rows};need(set(maps['equal_ensemble'])==set(target),'앙상블 대상')
  for fid,row in target.items():
   forecasts=[maps[k][fid] for k in ('recency','opponent','distribution')];q,t=average(forecasts);ensemble=maps['equal_ensemble'][fid];need(ensemble['event_date']==row['event_date'],'평가 날짜')
   for k in q:err=abs(q[k]-ensemble['probabilities_3way'][k]);need(err<1e-14,'3방향 평균');errs.append(err)
   for k in t:err=abs(t[k]-ensemble['probabilities_2way'][k]);need(err<1e-14,'조건부 정규화');errs.append(err)
   checked+=1
  for name,arr in rows.items():
   core.metrics(arr,{fid:{'home_score':1,'away_score':0} for fid in target}) # 실제 2026 정답이 아닌 형식 검사용 합성 값
 print(json.dumps({'상태':'전역선택_통합예측_감사통과','통합커밋':COMMIT,'선택SHA256':sha(sb),'예측SHA256':sha(pb),'전역선택':ranking[0],'전역순위':ranking,'개발731_승부적중':{k:v['decisive_hits'] for k,v in independent.items()},'개발앙상블_승부적중':em['decisive_hits'],'2026_가족별원본예측검사':dict(sources),'2026_앙상블평균검사':checked,'평균_최대오차':max(errs),'후보원본해시검사':len(sel['source_hashes']),'합성_2026_레이블_양극단_선택불변':True,'2026결과열람':False,'유의사항':'전역 선택과 통합 예측은 같은 Git 커밋에서 고정; 각 family 개발 선택은 각 family 최종 fit보다 먼저 고정'},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
