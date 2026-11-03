"""Synthetic restart and bounded-batch checks; no network or task installation."""
import copy, hashlib, json, subprocess, sys, tempfile
from datetime import timedelta
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward')
sys.path.insert(0,str(root/'research/kbo_forward'))
import runner,ledger,test_runner
commit='4860d514'
for path in ('research/kbo_forward/runner.py','research/kbo_forward/ledger.py','research/kbo_forward/locking.py'):
 assert (root/path).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+path]).replace(b'\r\n',b'\n')
if len(sys.argv)>1 and sys.argv[1]=='child':
 def wait(rows):
  print('LOCKED',flush=True)
  import time;time.sleep(30)
 ledger.append(Path(sys.argv[2]),wait)
 sys.exit(0)
with tempfile.TemporaryDirectory(prefix='kbo-runner-review-') as d:
 p=Path(d)/'lock-test.jsonl'
 proc=subprocess.Popen([sys.executable,'-B',__file__,'child',str(p)],stdout=subprocess.PIPE,text=True)
 assert proc.stdout.readline().strip()=='LOCKED'
 proc.kill();proc.wait(timeout=5)
 try:ledger.append(p,lambda rows:None);blocked=False
 except FileExistsError:blocked=True
 x=test_runner.payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2);x['evidence']['odds']['received_at']=now.isoformat()
 class Many(test_runner.Official):
  def __init__(self,x):
   super().__init__(x);self.games=[]
   for i in range(6):
    g=copy.deepcopy(self.game);g['source']['G_ID']='20261105test'+str(i);self.games.append(g)
  def schedule(self,dates):return {'games':self.games}
  def snapshot(self,g):
   self.snapshot_calls+=1;t=copy.deepcopy(self.x);t['event']['official_game_id']=g['source']['G_ID'];t['row']['fixture_id']=g['source']['G_ID']+str(t['event']['season']);t.pop('market');t['evidence'].pop('odds');return {'source_verified':True,'payload':t}
 def prices(state,events):
  qs=[]
  for e in events:
   y=copy.deepcopy(x);y['event'].update(e);qs.append(test_runner.quote(y))
  return {'quotes':qs,'excluded':[],'requests':[]}
 state=Path(d)/'batch';official=Many(x)
 a=runner.run_once(state,official,prices,clock=lambda:now,infer=test_runner.old.fake_infer)
 b=runner.run_once(state,official,prices,clock=lambda:now+timedelta(minutes=5),infer=test_runner.old.fake_infer)
 missing_sixth=a['captured_this_run']==5 and b['captured_this_run']==0 and official.snapshot_calls==5
 # The canonical wrapper checks byte hash but never derives terms from those bytes.
 source_dir=Path(d)/'prices'/'price-evidence';source_dir.mkdir(parents=True);raw=b'{"home":"1.80","away":"2.00"}';rawpath=source_dir/'body.json';rawpath.write_bytes(raw)
 q={'kind':'full_game_2way','settlement_rule':'draw_void_refund','market_id':'synthetic','odds':{'home':'9.99','away':'2.00'},'evidence':{'body_file':str(rawpath),'uri':'https://example.test','received_at':now.isoformat(),'sha256':hashlib.sha256(raw).hexdigest()}}
 market,receipt=runner.price_packet(q,d);mismatched_terms_accepted=market['odds']['home']!=receipt['body']['home']
 class Broken(test_runner.Official):
  def snapshot(self,g):raise ValueError('synthetic source failure')
 fail=runner.run_once(Path(d)/'failure',Broken(x),prices,clock=lambda:now,infer=test_runner.old.fake_infer)
 source_failure_status_ok=fail['status']=='ok' and len(fail['excluded'])==1
 assert not blocked and not missing_sixth and b['captured_this_run']==1 and official.snapshot_calls==6 and mismatched_terms_accepted and source_failure_status_ok
 print(json.dumps({'commit':commit,'killed_writer_leaves_blocking_ledger_lock':blocked,'sixth_candidate_starves_after_two_runs':missing_sixth,'first_run_captures':a['captured_this_run'],'second_run_captures':b['captured_this_run'],'price_terms_mismatch_accepted_by_wrapper':mismatched_terms_accepted,'source_failure_reported_status_ok':source_failure_status_ok},indent=2))
