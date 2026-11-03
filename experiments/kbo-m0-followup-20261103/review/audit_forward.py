"""고정 staging 코드를 합성 입력·임시 원장으로 검토한다."""
import copy,hashlib,json,subprocess,sys,tempfile,types
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;REPO=Path(r'C:\Users\user\Documents\Codex\kbmnext');C='01650a673d6a6f65afc1ae9f3127319a88cc266e';P='experiments/kbo-m0-followup-20261103/forward.py'
def need(x,msg):
 if not x:raise ValueError(msg)
def rejected(fn):
 try:fn()
 except ValueError:return True
 return False
def main():
 code=subprocess.check_output(['git','-C',str(REPO),'show',C+':'+P]);m=types.ModuleType('followup_forward_review');m.__file__=str(ROOT/'forward.py');sys.path.insert(0,str(ROOT/'vendor/evolution/common'));exec(compile(code,m.__file__,'exec'),m.__dict__)
 now=datetime(2026,11,3,4,0,tzinfo=timezone.utc);kick=now+timedelta(days=2,hours=8);row=copy.deepcopy(m.core.rows('test-actual.jsonl.gz')[-1]);gid='20261105test0';row.update(fixture_id=gid+'2026',event_date='2026-11-05',season=2026);event={'official_game_id':gid,'season':2026,'home_id':row['home_id'],'away_id':row['away_id'],'kickoff_at':kick.isoformat()};ev={}
 for kind in ('announcement','odds','history'):
  body={'synthetic':True,'kind':kind};ev[kind]={'uri':'https://www.koreabaseball.com/test' if kind!='odds' else 'https://www.betman.co.kr/test','received_at':now.isoformat(),'body':body,'sha256':m.digest(m.canonical(body))}
 x={'event':event,'market':{'market_id':'synthetic','kind':'full_game_2way','odds':{'home':'1.80','away':'1.90'},'settlement_rule':'draw_void_refund'},'row':row,'announcement':{'lineup_confirmed':True,'pitcher_announcement_confirmed':True,'basis':'official_confirmed_pregame_announcement','roster':copy.deepcopy(row['roster'])},'evidence':ev}
 need(m.validate_snapshot(x,now)==kick,'발표 선발 입력이 부당하게 거부됨');need(rejected(lambda:m.validate_snapshot(x,kick-timedelta(minutes=29))),'지각 거부 실패')
 # 사전등록된 최소 30분 조건에 따라 정각은 통과해야 한다.
 exact_allowed=not rejected(lambda:m.validate_snapshot(x,kick-timedelta(minutes=30)));need(exact_allowed,'사전등록된 정각 허용이 이뤄지지 않음')
 # 중첩 이력의 미래 수신 및 관측 시각을 재귀적으로 차단한다.
 later=copy.deepcopy(x);later['row']['raw_features']['home_lineup_strength']['received_at']=(now+timedelta(seconds=1)).isoformat();need(rejected(lambda:m.validate_snapshot(later,now)),'미래 수신 허용')
 later=copy.deepcopy(x);later['row']['raw_features']['home_lineup_strength']['value'][0]['history'][0]['observed_at']=(now+timedelta(seconds=1)).isoformat();need(rejected(lambda:m.validate_snapshot(later,now)),'미래 관측 허용')
 equal=copy.deepcopy(x);equal['row']['raw_features']['home_lineup_strength']['received_at']=now.isoformat();need(m.validate_snapshot(equal,now)==kick,'동일 시각 수신 거부')
 bad=copy.deepcopy(x);bad['announcement']['basis']='postgame_actual_start_verified';need(rejected(lambda:m.validate_snapshot(bad,now)),'경기 후 근거 거부 실패')
 bad=copy.deepcopy(x);bad['evidence']['history']['sha256']='0'*64;need(rejected(lambda:m.validate_snapshot(bad,now)),'본문 해시 오류 거부 실패')
 bad=copy.deepcopy(x);bad['market']['kind']='total_goals';need(rejected(lambda:m.validate_snapshot(bad,now)),'타 상품 거부 실패')
 bad=copy.deepcopy(x);bad['row']['raw_features']['home_lineup_strength']['value'][0]['history'][0]['date']=bad['row']['event_date'];need(rejected(lambda:m.validate_snapshot(bad,now)),'당일 이력 거부 실패')
 def fake(row):
  q={'fixture_id':row['fixture_id'],'event_date':row['event_date'],'probabilities_3way':{'home':.54,'draw':.1,'away':.36},'probabilities_2way':{'home':.6,'away':.4}};return {'baseline':q,'equal_ensemble':copy.deepcopy(q)},{'synthetic':'true'}
 with tempfile.TemporaryDirectory(prefix='codex-followup-audit-') as td:
  late_ledger=Path(td)/'late.jsonl';ticks=iter([kick-timedelta(minutes=31),kick-timedelta(minutes=29)]);need(rejected(lambda:m.capture(x,late_ledger,clock=lambda:next(ticks),infer=fake)) and not late_ledger.exists(),'예측 중 마감 경과 허용')
  ledger=Path(td)/'staging.jsonl';a=m.capture(x,ledger,clock=lambda:now,infer=fake);need(a['eligible_for_effectiveness_validation'] is False and a['source_verification']=='pending_independent_source_adapter','미검증 데이터 승격');before=ledger.read_bytes();changed=copy.deepcopy(x);changed['market']['odds']['home']='2.5';need(rejected(lambda:m.capture(changed,ledger,clock=lambda:now,infer=fake)) and ledger.read_bytes()==before,'동일 상품 덮어쓰기');need(m.status(ledger)['source_verified_paired_settlements']==0 and m.status(ledger)['formal_review_ready'] is False,'전향 성과로 승격')
  end=kick+timedelta(hours=4);body={'synthetic':True};e={'uri':'https://www.koreabaseball.com/test','received_at':end.isoformat(),'body':body,'sha256':m.digest(m.canonical(body))};settle={'event_key':a['event_key'],'official_game_id':gid,'season':2026,'home_id':row['home_id'],'away_id':row['away_id'],'game_date':row['event_date'],'status':'final','home_score':2,'away_score':2,'evidence':e}
  need(rejected(lambda:m.settle(settle,ledger,clock=lambda:now)),'경기 전 결과 거부 실패');z=m.settle(settle,ledger,clock=lambda:end);need(z['eligible_for_effectiveness_validation'] is False and all(q['net_units']=='0' and q['refund'] for q in z['paper_profits'].values()),'무승부 또는 미검증 상태');before=ledger.read_bytes();correction=copy.deepcopy(settle);correction['home_score']=3;need(rejected(lambda:m.settle(correction,ledger,clock=lambda:end)) and ledger.read_bytes()==before,'정정 덮어쓰기');status=m.status(ledger);need(status['source_verified_paired_settlements']==0 and status['formal_review_ready'] is False and 'ROI' not in str(status),'staging 결과를 전향 ROI로 표시')
  # 취소 역시 후행 별도 기록 및 환급만 한다.
  y=copy.deepcopy(x);y['event']['official_game_id']='20261105test1';y['row']['fixture_id']='20261105test12026';b=m.capture(y,ledger,clock=lambda:now,infer=fake);c=copy.deepcopy(settle);c.update(event_key=b['event_key'],official_game_id=y['event']['official_game_id'],status='cancelled',home_score=None,away_score=None);z=m.settle(c,ledger,clock=lambda:end);need(all(q['net_units']=='0' and q['refund'] for q in z['paper_profits'].values()) and z['eligible_for_effectiveness_validation'] is False,'취소 환급')
 print(json.dumps({'구현커밋':C,'forward_py_SHA256':hashlib.sha256(code).hexdigest(),'계약에맞는동작':{'T30정각_허용':exact_allowed},'수정후_차단':['미래수신','중첩미래관측','예측중마감경과'],'검사통과':['발표선발_경기전허용','경기후선발근거거부','해시오류거부','타상품거부','당일이력거부','동일상품덮어쓰기거부','경기전정산거부','무승부환급','취소환급','정정덮어쓰기거부','미인증표본0_ROI비표시'],'기록정정':'기존정산수정거부; 별도정정기록경로는아직없음','전향효과표본':0},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
