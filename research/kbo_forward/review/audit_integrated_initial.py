"""통합 근거 보존과 실패 시 관측 기회 처리를 합성 데이터로 검사한다."""
import gzip,hashlib,json,subprocess,sys,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');commit='d6e8581fb882328cb9ecdf1ac3fa3b4b5622e87b'
for n in ('connected.py','ledger.py','runner.py'):
 p='research/kbo_forward/'+n;assert (root/p).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+p]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import connected,ledger,runner
with tempfile.TemporaryDirectory(prefix='kbo-integrated-review-') as d:
 state=Path(d);folder=state/'verified-bundles';folder.mkdir();raw=b'{"responses":[]}';sha=hashlib.sha256(raw).hexdigest();f=folder/(sha+'.json.gz');f.write_bytes(gzip.compress(raw,mtime=0))
 payload={'evidence':{'history':{'body':{'archived_canonical_bundle_sha256':sha,'relative_file':'verified-bundles/'+sha+'.json.gz'}}}}
 connected.verify_archived_bundles(payload,state);f.write_bytes(gzip.compress(b'{"responses":[1]}',mtime=0))
 try:connected.verify_archived_bundles(payload,state);tamper_rejected=False
 except ValueError:tamper_rejected=True
 now=datetime(2026,11,3,4,tzinfo=timezone.utc);event={'official_game_id':'test','season':2026,'home_id':'SS','away_id':'HT','kickoff_at':(now+timedelta(hours=1)).isoformat()};ledger.observe_opportunity(event,state/'paired-ledger.jsonl',{},clock=lambda:now)
 class Broken:
  count=0
  def schedule(self,dates):raise ValueError('합성 원천 장애')
 def no_prices(*args):raise AssertionError('가격 수집은 실행되지 않아야 함')
 out=runner.run_once(state,Broken(),no_prices,clock=lambda:now+timedelta(minutes=40))
 records=ledger.read(state/'paired-ledger.jsonl');failure_leaves_missed_open=out['status']=='failed' and not any(r['type']=='missed_cutoff' for r in records)
 assert tamper_rejected and failure_leaves_missed_open
 print(json.dumps({'검토커밋':commit,'압축근거내용변조거부':tamper_rejected,'일정조회실패시마감기회미분류':failure_leaves_missed_open},ensure_ascii=False,indent=2))
