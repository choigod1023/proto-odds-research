"""고정 팀 코드 변환과 가격 원문 재검증을 임시 합성 자료로 검사한다."""
import copy,gzip,json,subprocess,sys,tempfile
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');commit='190b1c8bdab0ab342924c97bf13d81a8403349be'
for name in ('ledger.py','runner.py','features.py','prices.py'):
 p='research/kbo_forward/'+name
 assert (root/p).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+p]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import ledger,runner,test_runner
reverse={v:k for k,v in ledger.MODEL_TEAM_NAMES.items()};seen=set();checked=0
path=ledger.OLD/'vendor/evolution/common/inputs/train-actual.jsonl.gz'
with gzip.open(path,'rt',encoding='utf8') as f:
 for line in f:
  row=json.loads(line);teams={row['home_id'],row['away_id']}
  if not teams-set(seen):continue
  assert teams<=reverse.keys(),teams
  coded=copy.deepcopy(row);coded['home_id']=reverse[row['home_id']];coded['away_id']=reverse[row['away_id']]
  old,h=ledger.f.forecast(row);new,hh=ledger.infer_frozen(coded)
  assert old==new and h==hh and coded['home_id']==reverse[row['home_id']]
  seen.update(teams);checked+=1
  if len(seen)==len(reverse):break
assert seen==set(reverse)
with tempfile.TemporaryDirectory(prefix='kbo-source-review-') as d:
 x=test_runner.payload('full_game_2way');x['event'].update(home_id='SS',away_id='HT');x['row'].update(home_id='SS',away_id='HT')
 now=ledger.moment(x['event']['kickoff_at'])-test_runner.timedelta(hours=2);q=test_runner.quote(x,d,now);runner.price_packet(q,d,x['event'])
 bad=copy.deepcopy(q);bad['odds']['home']=99.99
 try:runner.price_packet(bad,d,x['event']);price_rejected=False
 except ValueError:price_rejected=True
 x['row']['history_coverage']={'home':{'status':'complete'},'away':{'status':'incomplete'}}
 try:runner.packet({'source_verified':True,'payload':x});coverage_rejected=False
 except ValueError:coverage_rejected=True
 assert price_rejected and coverage_rejected
print(json.dumps({'검토커밋':commit,'고정팀전체':len(seen),'실제고정학습행':checked,'고정예측완전일치':True,'가격원문불일치거부':price_rejected,'불완전이력차단':coverage_rejected},ensure_ascii=False,indent=2))
