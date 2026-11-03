"""고정 예측·결과·가격의 연도별·반기별 지표를 독립 재계산한다."""
import base64,collections,gzip,hashlib,io,json,subprocess,tarfile
from decimal import Decimal
from pathlib import Path
import reference as ref
R=Path(r'C:\Users\user\Documents\Codex\kbmfollowreview');BASE='experiments/kbo-m0-followup-20261103/';PRICE='f6681d7525e992ef52e770e50bf6ef2401c6ef42';HIST='7f06e76dfe77cbb3dd7d9395995133effdb1b66a';PRED='627cadca9096acae18a30f72484c342edab7acd3';OLD='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';O='experiments/kbo-player-effects-20261103/inputs/';E='experiments/kbo-m0-evolution-20261103/'
def git(c,p):
 repos={
  'e8ba21f970f73594848e75cc6515a1dd3a994bf1':r'C:\Users\user\Documents\Codex\kbt24',
  '11aa00812d45fdcf35e462413acfc48935dd530d':r'C:\Users\user\Documents\Codex\kbt25',
  '7f06e76dfe77cbb3dd7d9395995133effdb1b66a':r'C:\Users\user\Documents\Codex\kbmhold24',
  '627cadca9096acae18a30f72484c342edab7acd3':r'C:\Users\user\Documents\Codex\kbm0plus',
  'd08ccb708033fb6b067e84e80eae81ea3c9b4cfa':r'C:\Users\user\Documents\Codex\kbeffects'}
 return subprocess.check_output(['git','-C',repos.get(c,str(R)),'show',c+':'+p])
def sha(b):return hashlib.sha256(b).hexdigest()
def gz(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def need(x,msg):
 if not x:raise ValueError(msg)
def idx(rows):
 d={x['fixture_id']:x for x in rows};need(len(d)==len(rows),'중복 경기');return d
def winner(y):return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
def aggregate(labels,models,price,period):
 ids=sorted((f for f in labels if period(labels[f]['assumed_completed_date'])),key=lambda f:(labels[f]['assumed_completed_date'],f));priced=[f for f in ids if f in price];missing=set(ids)-set(priced);out={'전체경기':len(ids),'무승부':sum(winner(labels[f])=='draw' for f in ids),'가격경기':len(priced),'가격없는경기':len(missing),'가격없는무승부':sum(winner(labels[f])=='draw' for f in missing),'모델':{},'paired_기준대비':{}}
 need(all(set(models[k])>=set(ids) for k in models),'모델 모집단')
 for k in models:
  outcomes=ref.outcome_metrics([{'probabilities':models[k][f]['probabilities_3way'],'winner':winner(labels[f])} for f in ids]);bets=[];hits=refund=0
  for f in priced:
   p=models[k][f]['probabilities_2way'];q=[p['home'],p['away']];s=price[f];odds=[s['price_source']['prices']['home'],s['price_source']['prices']['away']];pick,z=ref.settle(q,odds,winner(labels[f]));hits+=int(pick==winner(labels[f]));refund+=int(winner(labels[f])=='draw');bets.append({'date':labels[f]['assumed_completed_date'],'p':q,'prices':odds,'winner':winner(labels[f])})
  ledger=ref.ledger_metrics(bets);out['모델'][k]={'전체결과':outcomes,'가격적중':hits,'환급':refund,'net':str(ledger['net']),'roi':float(ledger['roi']) if ledger['roi'] is not None else None,'월별':{key:str(v) for key,v in sorted(ledger['monthly'].items())},'일마감낙폭':str(ledger['drawdown']) if ledger['drawdown'] is not None else None}
  if k!='baseline':
   pairs=[]
   for f in priced:
    a=models[k][f]['probabilities_2way'];b=models['baseline'][f]['probabilities_2way'];s=price[f];pairs.append({'date':labels[f]['assumed_completed_date'],'a':[a['home'],a['away']],'b':[b['home'],b['away']],'prices':[s['price_source']['prices']['home'],s['price_source']['prices']['away']],'winner':winner(labels[f])})
   out['paired_기준대비'][k]=ref.paired_weekly(pairs)
 return out

def main():
 hand=json.loads(git(PRICE,BASE+'data/handoff.json'));need(hand['contract_sha256']=='c56b5675def7e54783b6e67c87e09c5d03d405aba90d12fa07b0fb577be8f7ef','계약');data={}
 for name,entry in hand['files'].items():
  raw=git(PRICE,entry['path']);need(sha(raw)==entry['sha256'],'가격 입력 해시 '+name);data[name]=idx(gz(raw))
 rawrows=idx(gz(git(OLD,O+'rows.jsonl.gz')));priorlabels=idx(gz(git(OLD,O+'source-labels.jsonl.gz')))
 # 가격 행이 이전 원본 market의 정확한 양면 가격이며 제외·무승부 표본이 따로 남았는지 확인한다.
 for year in (2024,2025):
  labels=data['labels-'+str(year)];price=data['prices-'+str(year)];excluded=data['exclusions-'+str(year)];need(set(price).isdisjoint(excluded) and set(price)|set(excluded)==set(labels),'가격 교집합')
  for fid,y in labels.items():
   original=rawrows[fid];need(all(y[k]==priorlabels[fid][k] for k in ('home_score','away_score')),'과거 결과 원본 '+fid)
   if fid in price:
    x=price[fid];m=original['market'];need(m and m['kind']=='full_game_2way' and m['market_id']==x['price_source']['market_id'] and m['selections']==x['price_source']['prices'] and x['winner']==winner(y) and x['home_id']==original['home_id'] and x['away_id']==original['away_id'],'가격 원본 '+fid)
    need(x['price_source']['execution_price_verified'] is False and x['price_source']['historical_quote_receipt_proven'] is False and x['price_source']['odds_observed_at'] is None,'가격 수신 주장 '+fid)
   else:need(original['market'] is None and excluded[fid]['reasons']==['market_missing'],'누락 사유 '+fid)
  need(sum(winner(y)=='draw' for y in labels.values())==(9 if year==2024 else 22) and not any(winner(labels[f])=='draw' for f in price),'무승부 표본 경계')
 # 기존 수집 원문의 일정 점수는 현재 확인된 공식 응답이지 과거 수신 증거가 아니다.
 a24='e8ba21f970f73594848e75cc6515a1dd3a994bf1';p24='experiments/kbo-actual-2024-20261103/';roster24=idx([json.loads(x) for x in git(a24,p24+'rosters.jsonl').splitlines()]);bundle24=git(a24,p24+'official-evidence.tar.gz');need(sha(bundle24)=='512bed8407fca4132d2497e2dcc7b1ee43cd92133d7ffbe1e65d4ff4eae88800','2024 원문 해시');t=tarfile.open(fileobj=io.BytesIO(bundle24),mode='r:gz');cache={}
 for fid,y in data['labels-2024'].items():
  r=roster24[fid];h=r['evidence']['schedule_evidence']['sha256'];b=cache.get(h)
  if b is None:b=gzip.decompress(t.extractfile('raw/'+h+'.gz').read());need(sha(b)==h,'2024 일정 해시');cache[h]=b
  g=[z for z in json.loads(b)['game'] if z['G_ID']==r['official_game_id']];need(len(g)==1 and str(g[0]['GAME_STATE_SC'])=='3' and (int(g[0]['B_SCORE_CN']),int(g[0]['T_SCORE_CN']))==(y['home_score'],y['away_score']),'2024 공식 결과 '+fid)
 a25='11aa00812d45fdcf35e462413acfc48935dd530d';p25='experiments/kbo-actual-lineups-2025-20261103/';roster25=idx(gz(git(a25,p25+'actual-lineups-2025.jsonl.gz')));raw25=git(a25,p25+'raw-evidence.jsonl.gz');need(sha(raw25)=='8a214d0e87c8dd84aa9178ae0c2bb8786dadb8c5c6eb4320e6d00e8943833b56','2025 원문 해시');evidence={}
 for z in gz(raw25):
  if z.get('body_base64') is not None:
   b=base64.b64decode(z['body_base64'],validate=True);need(sha(b)==z['sha256'],'2025 응답 해시');evidence[z['sha256']]=b
 for fid,y in data['labels-2025'].items():
  r=roster25[fid];b=evidence[r['schedule_evidence']['sha256']];g=[z for z in json.loads(b)['game'] if z['G_ID']==r['official_game_id']];need(len(g)==1 and str(g[0]['GAME_STATE_SC'])=='3' and (int(g[0]['B_SCORE_CN']),int(g[0]['T_SCORE_CN']))==(y['home_score'],y['away_score']),'2025 공식 결과 '+fid)
 hraw=git(HIST,BASE+'historical/predictions2024.json');need(sha(hraw)=='2643db9ee19e01de8a31ac13316ad0b2c05366ccf5ffb911b4b8b865a0d4f92b','2024 예측 SHA');h=json.loads(hraw);models24={k:idx(h[v]) for k,v in [('baseline','baseline'),('recency','shrink'),('distribution','nb03'),('equal_ensemble','equal_ensemble')]}
 sraw=git(PRED,E+'artifacts/selection.json');praw=git(PRED,E+'artifacts/predictions.json');need(sha(sraw)=='ec6fc195fcb8dffde75a73f00e482332048de395e2665327c1af1c2ab15c832a' and sha(praw)=='8749562073997c4e2374effe3b53fa41efcd2e4e1a60f38ab658496633e732b4','2025/26 동결 예측');sel=json.loads(sraw)['baseline_and_nominee_validation_predictions'];models25={k:idx(sel[k]) for k in ('baseline','recency','distribution')};opponent=idx(sel['opponent']);ensemble={}
 for f,r in models25['baseline'].items():
  a=[models25['recency'][f],opponent[f],models25['distribution'][f]];q={k:sum(z['probabilities_3way'][k] for z in a)/3 for k in ('home','draw','away')};den=q['home']+q['away'];ensemble[f]={'probabilities_3way':q,'probabilities_2way':{k:q[k]/den for k in ('home','away')}}
 models25['equal_ensemble']=ensemble;models26={k:idx(v) for k,v in json.loads(praw)['predictions']['actual'].items() if k in ('baseline','recency','distribution','equal_ensemble')};labels26=idx(gz(git(OLD,O+'test-labels.jsonl.gz')));labels26={f:labels26[f] for f in models26['baseline']};oldprices=json.loads(git(OLD,O+'price-ledgers.json'))['ledgers'];reject=idx(gz(git(OLD,O+'price-reject-audit.jsonl.gz')))
 periods={'전체':lambda d:True,'상반기':lambda d:int(d[5:7])<=6,'하반기':lambda d:int(d[5:7])>=7};result={}
 for year,labels,models,price in [(2024,data['labels-2024'],models24,data['prices-2024']),(2025,data['labels-2025'],models25,data['prices-2025']),(2026,labels26,models26,idx(oldprices['extended_day_team_source_confirmed']))]:
  result[str(year)]={k:aggregate(labels,models,price,fn) for k,fn in periods.items()}
  if year==2026:
   tiers={k:idx(v) for k,v in oldprices.items()};
   for name,raw in list(tiers.items()):tiers[name+'_current_reject_excluded']={f:r for f,r in raw.items() if r['price_source']['origin']=='official_restored' or reject[f]['official_result']['buyReject']=='0'}
   result[str(year)]['가격민감도']={k:aggregate(labels,models,p,lambda d:True) for k,p in tiers.items()}
 print(json.dumps({'상태':'독립계산_완료','가격커밋':PRICE,'예측커밋_2024':HIST,'예측커밋_2025_2026':PRED,'2024_공식점수확인':len(data['labels-2024']),'2025_공식점수확인':len(data['labels-2025']),'가격결측전체무승부':{'2024':9,'2025':22},'반기별결과':result,'주의':'2024/25 가격표본은 무승부 전체 누락·사후점수 선택; 후향 자료·과거 수신/체결 미증명'},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
