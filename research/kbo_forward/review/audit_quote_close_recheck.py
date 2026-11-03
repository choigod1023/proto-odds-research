"""고정 가격 packet과 미배당 정산을 합성 원문으로 독립 검사한다."""
import copy,json,subprocess,sys,tempfile
from datetime import timedelta
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');commit='d6e8581fb882328cb9ecdf1ac3fa3b4b5622e87b'
for name in ('ledger.py','runner.py','features.py','prices.py'):
 p='research/kbo_forward/'+name
 assert (root/p).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+p]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import ledger,runner,prices,test_runner,test_prices,test_ledger
with tempfile.TemporaryDirectory(prefix='kbo-price-review-') as d:
 state=Path(d);x=test_runner.payload('full_game_2way');x['event'].update(home_id='SS',away_id='HT');x['row'].update(home_id='SS',away_id='HT')
 now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2);close=now+timedelta(minutes=1)
 target=state/'prices/price-evidence';target.mkdir(parents=True)
 row=test_prices.ROW|{'homeId':'SS','awayId':'HT','gameDate':test_prices.ms(ledger.moment(x['event']['kickoff_at'])),'endDate':test_prices.ms(close),'winAllot':1.8,'loseAllot':1.9}
 round_=test_prices.ROUND|{'saleEndDate':test_prices.ms(close)};body=test_prices.detail([row]);listing=json.dumps({'protoGames':[round_]}).encode()
 for raw in (body,listing):(target/(ledger.digest(raw)+'.body')).write_bytes(raw)
 ev={'uri':prices.DETAIL_URL,'body_file':str(target/(ledger.digest(body)+'.body')),'sha256':ledger.digest(body),'received_at':now.isoformat(),'round_listing_sha256':ledger.digest(listing)}
 qs,_=prices.parse_detail(body,round_,[x['event']],now.isoformat(),ev);q=qs[0]
 changed=copy.deepcopy(q);changed['close_at']=(close+timedelta(hours=1)).isoformat();changed['round_close_at']=changed['close_at'];changed['source_product']['betNm']='변조상품'

 try:runner.price_packet(changed,state,x['event']);terms_changed=True
 except ValueError:terms_changed=False
 market,receipt=runner.price_packet(q,state,x['event']);x['market']=market;x['evidence']['odds']=receipt

 try:ledger.capture(x,state/'closed.jsonl',clock=lambda:now+timedelta(minutes=2),infer=test_runner.old.fake_infer);after_close=True
 except ValueError:after_close=False
 u=copy.deepcopy(x);u.pop('market');u['evidence'].pop('odds');p=state/'unpriced.jsonl';c=ledger.capture_unpriced(u,p,clock=lambda:now,infer=test_runner.old.fake_infer)
 result,end=test_ledger.result(u,c,h=4,a=2);ledger.settle(result,p,clock=lambda:end);s=ledger.summary(p,now=end)
 unpriced_isolated=s['paired_settled']==0 and s['groups']=={} and all(z['n']==1 and z['roi'] is None for z in s['unpriced_outcome_comparison'].values())
 assert not terms_changed and not after_close and unpriced_isolated
 print(json.dumps({'검토커밋':commit,'판매마감과상품표기변조가원문대조통과':terms_changed,'실제판매마감후캡처허용':after_close,'미배당정산ROI표본제외':unpriced_isolated},ensure_ascii=False,indent=2))
