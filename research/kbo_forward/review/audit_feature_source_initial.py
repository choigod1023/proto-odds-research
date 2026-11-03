"""고정 피처 생성기의 입력 신뢰 경계를 합성 원문으로 재현한다."""
import hashlib,subprocess,sys
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward')
commit='4b62ac97ac39f6a229364e88ee921351bb39330a'
path='research/kbo_forward/features.py'
assert (root/path).read_bytes().replace(b'\r\n',b'\n')==subprocess.check_output(['git','-C',str(root),'show',commit+':'+path]).replace(b'\r\n',b'\n')
sys.path.insert(0,str(root/'research/kbo_forward'))
import features,test_features
x,r,h=test_features.data()
for team in ('AA','BB'):
 s=h['schedules'][team];s['body']='{"game":[]}';s['sha256']=hashlib.sha256(s['body'].encode()).hexdigest()
row=features.build_row(x,r,h,test_features.NOW)
assert row['features']['home_team_strength']['value']==2.5
assert row['features']['home_lineup_strength']['value']==4/7
print('일정 원문 game=[]와 별도 events의 경기 1건이 서로 달라도 팀 피처를 산출함')
print('이 함수는 호출자 입력을 신뢰하므로 공식 원천 연계에서 원문 파싱 일치 검사가 필요함')
