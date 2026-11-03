"""공식 경기 별칭이 점수 증거 없이 병합되는지 합성 입력으로 검사한다."""
import subprocess,sys
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward');commit='32b76ce3329fccae5ad8743e07e94bd4d63df67c';p='research/kbo_forward/official.py'
assert (root/p).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+p]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import official
x={'G_ID':'test','SEASON_ID':'2026','G_DT':'20261102','HOME_NM':'삼성','AWAY_NM':'KIA','HOME_ID':'SS','AWAY_ID':'HT','B_SCORE_CN':'5','T_SCORE_CN':'3'};alias='score:2026-11-02:삼성:KIA'
seed={'games':[{'fixture_id':alias,'date':'2026-11-02','teams':{'SS':{'relief':{'outs':4}}}}]}
accepted=official.history_fixture_id(x,[x],seed)==alias
assert accepted
print('점수가 없는 seed 경기 별칭이 공식 경기와 같다고 병합됨:',accepted)

required={'KT':{'pitcher':{'김정운'},'hitter':{'타자'}}}
h={'games':[{'unresolved_identities':[{'team':'KT','role':'pitcher','name':'김정운'}]}]}
assert official.unresolved_required(h,required)
h['games'][0]['unresolved_identities'][0]['name']='다른선수'
assert not official.unresolved_required(h,required)
game2=dict(x,G_ID='another')
try:official.history_fixture_id(x,[x,game2],seed);doubleheader_rejected=False
except official.SourceError:doubleheader_rejected=True
assert doubleheader_rejected
import features
frozen=features.seed_from_frozen('2026-11-03T09:00:00+00:00','2026-11-04')
rows=[g for g in frozen['games'] if g['fixture_id'].startswith('score:')]
missing=[g for g in rows if not any(t.get('runs_for') is not None and t.get('runs_against') is not None for t in g.get('teams',{}).values())]
assert not missing
print('선발 동명이인 차단, 다른 선수 허용, 같은 팀 더블헤더 차단: 통과')
print('실제 고정 seed 점수 별칭:',len(rows),'점수 양쪽 모두 없는 별칭:',len(missing))
