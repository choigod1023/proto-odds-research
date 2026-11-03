"""공식 어댑터의 아직 연결되지 않은 경로를 합성 일정으로 확인한다."""
import json,subprocess,sys,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');sys.path.insert(0,str(root))
commit='4b894395e930449441c2ce3f0614412e15df3f56'
path='research/kbo_forward/official.py'
assert (root/path).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+path]).replace(b'\r\n',b'\n')
import research.kbo_forward.official as off
kst=timezone(timedelta(hours=9));kick=(datetime.now(timezone.utc)+timedelta(days=1)).astimezone(kst);stamp=datetime.now(timezone.utc).isoformat()
g={'G_ID':'20261105test0','G_DT':kick.strftime('%Y%m%d'),'G_TM':kick.strftime('%H:%M'),'HOME_ID':'SS','AWAY_ID':'HT','HOME_NM':'삼성','AWAY_NM':'KIA','LE_ID':'1','SR_ID':'0','SEASON_ID':str(kick.year),'CANCEL_SC_ID':'0','GAME_STATE_SC':'1','START_PIT_CK':1,'B_PIT_P_ID':'11','B_PIT_P_NM':'선발1','T_PIT_P_ID':'22','T_PIT_P_NM':'선발2'}
rec={'params':{'date':g['G_DT']},'finished_at':stamp};item={'source':g,'receipt':rec}
with tempfile.TemporaryDirectory(prefix='kbo-official-review-') as d:
 c=off.OfficialClient(d);lineup={side:[{'name':side+str(i)} for i in range(9)] for side in ('home','away')};ids={side:[side+str(i) for i in range(9)] for side in ('home','away')};seen=[]
 with patch.object(c,'verified_body',return_value=json.dumps({'game':[g]}).encode()),patch.object(c,'fetch',return_value=(b'[]',{'finished_at':stamp})),patch.object(off,'parse_lineup',return_value=lineup),patch.object(c,'resolve_lineups',return_value=(ids,[])),patch.object(c,'verify_history_coverage',side_effect=lambda event,history:seen.append(event) or False):
  default=c.snapshot(item)
  wired=c.snapshot(item,history={'seed':{}},build_row=lambda *x:None,verify_history=lambda *x:True)
 assert default['source_verified'] is False and 'verified_history_features_not_attached' in default['missing']
 assert seen[0]['home_id']=='삼성' and seen[0]['home_id']!=g['HOME_ID']
 print('기본 snapshot 경로: 이력 미연결로 검증 실패')
 print('이력 검증에 전달된 홈 팀: 삼성; 공식 일정의 홈 코드: SS')
