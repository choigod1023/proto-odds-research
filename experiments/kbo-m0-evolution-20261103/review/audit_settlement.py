"""동결 예측 뒤 2026 결과·가격을 별도로 읽어 독립 정산한다."""
import collections,gzip,hashlib,json,math,subprocess,sys
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
import reference as ref
from audit_recency import same,need
REPO=Path(r'C:\Users\user\Documents\Codex\kbm0plus');BASE='experiments/kbo-m0-evolution-20261103/';PRED='627cadca9096acae18a30f72484c342edab7acd3';RESULT='9495a91512d19e80d158a545d21d7311023ac36c';OLD='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';OB='experiments/kbo-player-effects-20261103/inputs/'
def git(rev,path):return subprocess.check_output(['git','-C',str(REPO),'show',rev+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def idx(rows):
 out={r['fixture_id']:r for r in rows};need(len(out)==len(rows),'중복 경기');return out
def winner(y):
 need(y['status']=='final' and all(type(y[k]) is int and y[k]>=0 for k in ('home_score','away_score')),'결과 상태');return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
def outcome_pair(rows,name):
 groups=collections.defaultdict(lambda:np.zeros(4));changed=0
 for x in rows:
  a=x['preds'][name];b=x['preds']['baseline'];y=x['winner']
  def score(q):return np.array([int(max(('home','draw','away'),key=q.get)==y),-math.log(max(q[y],1e-15)),sum((q[k]-int(k==y))**2 for k in ('home','draw','away'))])
  groups[date.fromisoformat(x['date']).isocalendar()[:2]]+=np.r_[1,score(a)-score(b)];changed+=max(a,key=a.get)!=max(b,key=b.get)
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(1)
 return {'weeks':len(arr),'changed3way_picks':changed,**{key:np.quantile(boot[:,i]/boot[:,0],[.025,.975]).tolist() for i,key in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1)}}
def main():
 subprocess.check_call(['git','-C',str(REPO),'merge-base','--is-ancestor',PRED,RESULT]);pr=git(PRED,BASE+'artifacts/predictions.json');need(sha(pr)=='8749562073997c4e2374effe3b53fa41efcd2e4e1a60f38ab658496633e732b4' and pr==git(RESULT,BASE+'artifacts/predictions.json'),'예측 동결 바이트 변경');pred=json.loads(pr)
 rr=git(RESULT,BASE+'artifacts/results.json');need(sha(rr)=='44c9d1bdc20da037670bcfc68f5af8ee1b548990397718bc731fb9b7ea287049','결과 SHA');published=json.loads(rr)
 need(pred['test_outcomes_loaded'] is False and pred['candidate_choice_used2026'] is False and published['prediction_commit']==PRED and published['prediction_sha256']==sha(pr) and published['primary_preselected_on2025']==pred['primary']=='recency' and published['actual_wallet_replay'] is False and published['model_adoption'] is False,'동결·후향 상태')
 hashes={'test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','price-ledgers.json':'24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23','price-reject-audit.jsonl.gz':'629c26c049669b07c6f0fc7413cd9c55a86beba24b20ce75767f2f54e82a2d93'};source={k:git(OLD,OB+k) for k in hashes}
 for k,h in hashes.items():need(sha(source[k])==h and published['evaluation_input_sha256'][k]==h,'평가 입력 해시 '+k)
 labels=idx([json.loads(x) for x in gzip.decompress(source['test-labels.jsonl.gz']).splitlines()]);reject=idx([json.loads(x) for x in gzip.decompress(source['price-reject-audit.jsonl.gz']).splitlines()]);prices=json.loads(source['price-ledgers.json'])['ledgers'];need(published['evaluation_inputs_commit']==OLD,'결과 원천 커밋')
 prior=json.loads(git('f4a3e3d300e02406601d844f86a6eb50d54a76ec','experiments/kbo-actual-training-review-20261103/audit-independent-settlement.json'));need(prior['score_source_commit']==OLD and prior['official_score_comparison']['matched']==709 and prior['official_score_comparison']['mismatch']==0 and prior['official_score_comparison']['missing']==0,'이전 공식 점수 대조 근거')
 counts=collections.Counter();summaries={};names={'baseline','recency','opponent','distribution','equal_ensemble','draw_hurdle'}
 for scenario,sets in pred['predictions'].items():
  need(set(sets)==names,'모델 집합');models={k:idx(v) for k,v in sets.items()};ids=set(models['baseline']);need(len(ids)==709 and all(set(v)==ids for v in models.values()),'709 동일 모집단');worker=published['results'][scenario];outrows=[]
  for fid in sorted(ids,key=lambda f:(models['baseline'][f]['event_date'],f)):
   truth=winner(labels[fid]);outrows.append({'fixture_id':fid,'date':models['baseline'][fid]['event_date'],'winner':truth,'preds':{name:models[name][fid]['probabilities_3way'] for name in names}})
  for name in names:
   z=ref.outcome_metrics([{'probabilities':r['preds'][name],'winner':r['winner']} for r in outrows]);expected=worker['outcome'][name]
   for left,right in (('n','n'),('hits','hits'),('accuracy','accuracy'),('logloss','logloss'),('brier','brier'),('decisive','decisive'),('decisive_hits','decisive_hits'),('decisive_accuracy','conditional_accuracy'),('decisive_logloss','conditional_logloss'),('decisive_brier','conditional_brier'),('mean_predicted_draw','mean_draw_probability')):need(math.isclose(expected[left],z[right],rel_tol=1e-11,abs_tol=1e-11),'결과 지표 '+scenario+' '+name+' '+left)
   need(math.isclose(expected['observed_draw_rate'],z['draws']/z['n'],abs_tol=1e-14),'무승부 비율');counts['결과모델']+=1
   if name!='baseline':same(outcome_pair(outrows,name),worker['outcome_paired_vs_baseline'][name],'결과 paired '+scenario+' '+name);counts['결과paired']+=1
  summaries[scenario]={}
  for tier,source_rows in prices.items():
   base=[r for r in source_rows if r['fixture_id'] in ids];need(len(idx(base))==len(base),'가격 중복')
   for suffix,rows in (('',base),('_current_reject_excluded',[r for r in base if r['price_source']['origin']=='official_restored' or reject[r['fixture_id']]['official_result']['buyReject']=='0'])):
    key=tier+suffix;g=worker['price_cohorts'][key];need(g['n']==len(rows),'가격 표본 수');ledger_ref={};all_records=[]
    for name in names:
     rr0=[];bet=[];hits=void=0;monthly=collections.defaultdict(Decimal)
     for r in rows:
      fid=r['fixture_id'];y=winner(labels[fid]);need(y==r['winner'] and r['date']==models[name][fid]['event_date'],'같은 경기·결과');p=models[name][fid]['probabilities_2way'];q=[p['home'],p['away']];odds=[r['price_source']['prices']['home'],r['price_source']['prices']['away']];pick,profit=ref.settle(q,odds,y);hit=y!='draw' and pick==y;hits+=hit;void+=y=='draw';monthly[r['date'][:7]]+=profit
      rr0.append({'fixture_id':fid,'date':r['date'],'winner':y,'pick':pick,'net':str(profit),'hit':hit});bet.append({'date':r['date'],'p':q,'prices':odds,'winner':y});counts['개별정산']+=1
     calc=ref.ledger_metrics(bet);w=g['models'][name];need(w['n']==len(rows) and w['hits']==hits and w['refunds']==void and w['decisive']==len(rows)-void and math.isclose(w['decisive_accuracy'],hits/(len(rows)-void),abs_tol=1e-12),'정산 건수 '+scenario+' '+key+' '+name);need(Decimal(w['net_units'])==calc['net'] and math.isclose(w['roi'],float(calc['roi']),abs_tol=1e-12) and Decimal(w['max_daily_close_drawdown_units'])==calc['drawdown'],'손익·낙폭 '+name)
     need(set(w['monthly_units'])==set(monthly) and all(Decimal(w['monthly_units'][k])==v for k,v in monthly.items()) and w['profitable_months']==sum(v>0 for v in monthly.values()),'월별 손익 '+name)
     pub=published['ledgers'][scenario][key][name];need(len(pub)==len(rr0),'원장 수')
     for a,b in zip(pub,rr0):
      need(all(a[k]==b[k] for k in ('fixture_id','date','winner','pick','hit')) and Decimal(a['net'])==Decimal(b['net']),'개별 정산 '+name)
     ledger_ref[name]=bet;counts['가격모델']+=1
    for name in names-{'baseline'}:
     all_rec=[]
     for a,b in zip(ledger_ref[name],ledger_ref['baseline']):
      need(a['date']==b['date'] and a['winner']==b['winner'] and a['prices']==b['prices'],'동일 가격 비교');all_rec.append({'date':a['date'],'winner':a['winner'],'prices':a['prices'],'a':a['p'],'b':b['p']})
     q=ref.paired_weekly(all_rec);p=g['paired_candidate_minus_baseline'][name];need(p['weeks']==q['weeks'] and p['changed_picks']==q['changed'],'paired 픽');same(p['roi_delta_ci95'],q['roi_ci95'],'ROI 구간');same(p['decisive_accuracy_delta_ci95'],q['accuracy_ci95'],'승부적중 구간');counts['가격paired']+=1
    if key=='extended_day_team_source_confirmed':summaries[scenario]={name:{'적중709':worker['outcome'][name]['hits'],'가격경기수':g['n'],'순손익':g['models'][name]['net_units'],'ROI':g['models'][name]['roi'],'일마감낙폭':g['models'][name]['max_daily_close_drawdown_units']} for name in names}
  need(models['opponent']==models['baseline'] and all(models['draw_hurdle'][f]['probabilities_2way']==models['baseline'][f]['probabilities_2way'] for f in ids),'기준 통제 불일치')
 print(json.dumps({'상태':'최종정산_독립대조_통과','예측커밋':PRED,'예측SHA256':sha(pr),'정산커밋':RESULT,'결과SHA256':sha(rr),'결과원천커밋':OLD,'검사수':counts,'시나리오별_확장가격요약':summaries,'과거수신및체결증명':False,'이전공식점수대조_동일레이블709':True,'모델채택':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
