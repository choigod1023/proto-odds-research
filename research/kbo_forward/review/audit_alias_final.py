"""점수 없는 별칭과 독립 작업 디렉터리 import를 합성 입력으로 검사한다."""
import subprocess,sys,tempfile
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');commit='a721357271677ecab373e7cb0fdce5e58bd61b56'
for f in ('official.py','runner.py'):
 p='research/kbo_forward/'+f
 assert (root/p).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+p]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import official
x={'G_ID':'test','SEASON_ID':'2026','G_DT':'20261102','HOME_NM':'삼성','AWAY_NM':'KIA','HOME_ID':'SS','AWAY_ID':'HT','B_SCORE_CN':'5','T_SCORE_CN':'3'};alias='score:2026-11-02:삼성:KIA'
for stats in ({},{'runs_for':5},{'runs_against':3}):
 seed={'games':[{'fixture_id':alias,'date':'2026-11-02','teams':{'SS':stats}}]}
 try:official.history_fixture_id(x,[x],seed);raise AssertionError('불완전 점수 허용')
 except official.SourceError as e:assert 'complete_score_missing' in str(e)
seed={'games':[{'fixture_id':alias,'date':'2026-11-02','teams':{'SS':{'runs_for':5,'runs_against':3}}}]};assert official.history_fixture_id(x,[x],seed)==alias
with tempfile.TemporaryDirectory(prefix='kbo-path-review-') as td:
 script=root/'research/kbo_forward/runner.py'
 source="import runpy,sys;sys.argv=[sys.argv[1],'--status','--state',sys.argv[2]];runpy.run_path(sys.argv[0],run_name='__main__');import research.kbo_forward.official;print('IMPORT_OK')"
 result=subprocess.run([sys.executable,'-I','-B','-c',source,str(script),str(script),td],cwd=td,capture_output=True,text=True,encoding='utf-8',check=True)
 assert 'IMPORT_OK' in result.stdout
print('완전 점수 없는 별칭 3종 거부, 일치하는 점수 별칭 허용')
print('별도 작업 디렉터리와 격리 모드에서 runner 및 research 패키지 import 성공')
