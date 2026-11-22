import sys
from pathlib import Path
from copy import deepcopy
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nba_shot_profile_model as m

def side():
    return {'estimated_possessions':90.,'attempts':[20,10,8,5,0,5,20,10,0],'makes':[12,5,4,2,0,2,7,3,0],'fta':20,'ftm':15}
def sample():
    return [{'game_id':str(i),'day':f'2025-01-{i+1:02}','teams':['BOS','LAL'],'sides':[side(),side()]} for i in range(10)]

def test_equal_sides_and_counts():
    v=m.arr(side());q=m.aggregate(v*10,v*10,v*20,False);s=m.aggregate(v*10,v*10,v*20,True)
    assert np.allclose(q,s)
    assert m.probability([q*90,q*90])==pytest.approx(.5,abs=1e-10)

def test_rare_zone_zero_attempt_and_zero_success():
    a=side();a['attempts'][4]=1;a['makes'][4]=0;v=m.arr(a)
    for spatial in (False,True):
        out=m.aggregate(v,v,v,spatial);assert np.all(np.isfinite(out)) and np.all(out>=0)

def test_future_target_box_and_score_cannot_change_feature():
    history=sample(); target={'day':'2025-01-20','teams':['BOS','LAL'],'scores':[100,90]}
    before,reason=m.feature(target,history); assert reason is None
    later=deepcopy(history);later.append(dict(deepcopy(history[0]),game_id='future',day='2025-01-20'))
    changed=dict(target,scores=[1,999],sides=[{},{}]);after,reason=m.feature(changed,later)
    assert reason is None and before==after

def test_window_boundaries_and_minimum():
    h=sample(); assert len(m.window(h,'2025-01-08'))==4
    assert m.feature({'day':'2025-01-08','teams':['BOS','LAL']},h)[1]=='insufficient_team_history'
    assert m.feature({'day':'2025-01-09','teams':['BOS','LAL']},h)[1] is None

def test_score_identity_and_comparison():
    p=m.probability([[30,12,15],[30,12,15]])
    assert p==pytest.approx(.5,abs=1e-10)
    rows=[{'day':'2025-01-01','home_win':1,'odds':[2,2],'probabilities':{'a':.5,'b':.4}}]
    assert m.calc(rows,'a')['wins']==1 and m.calc(rows,'b')['wins']==0

def test_training_and_serialization_stable():
    rows=[{'home_win':1,'market':.6,'feature_delta':.1},{'home_win':0,'market':.6,'feature_delta':-.2}]
    assert m.fit_beta(rows)==m.fit_beta(rows)
    assert m.gzip.compress(b'NBA\n',mtime=0)==m.gzip.compress(b'NBA\n',mtime=0)


def test_precise_calendar_boundaries():
    t={'day':'2026-01-10','teams':['BOS','LAL']}
    from datetime import date,timedelta
    d=date.fromisoformat(t['day'])
    records=[{'game_id':str(n),'day':(d-timedelta(days=n)).isoformat()} for n in (366,365,4,3)]
    assert [x['game_id'] for x in m.window(records,t['day'])]==['365','4']
