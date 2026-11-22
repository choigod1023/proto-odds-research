import sys
from pathlib import Path
from datetime import date,timedelta
from copy import deepcopy
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nba_player_spatial_model as m

def test_projected_minutes_and_missing_recent_game():
    start=date(2025,1,1);hist=[{'id':str(i),'day':(start+timedelta(days=i)).isoformat()} for i in range(11)]
    boxes={str(i):{'BOS':{'x':100,'y':140}} for i in range(11)}
    result,reason=m.projected_rotation('BOS','2025-01-16',{'BOS':hist},boxes)
    assert reason is None and result['history_game_ids']==[str(i) for i in range(1,11)]
    assert sum(result['minutes'].values())==pytest.approx(240)
    boxes['10']=None;assert m.projected_rotation('BOS','2025-01-16',{'BOS':hist},boxes)[1]=='missing_or_invalid_recent_box'

def test_future_and_target_activity_do_not_affect_rotation():
    start=date(2025,1,1);h=[{'id':str(i),'day':(start+timedelta(days=i)).isoformat()} for i in range(10)];boxes={str(i):{'BOS':{'a':48,'b':192}} for i in range(10)}
    before=m.projected_rotation('BOS','2025-01-20',{'BOS':h},boxes)
    later=h+[{'id':'target','day':'2025-01-20','active':['z'],'minutes':999}];boxes['target']={'BOS':{'z':240}}
    assert m.projected_rotation('BOS','2025-01-20',{'BOS':later},boxes)==before

def test_player_shot_aggregate_equal_and_cold_prior():
    a=np.array([10,10,10,10,0,10,10,10,0]);made=a*.5; rot={'a':120.,'b':120.};states={'a':{'games':5,'minutes':240.,'attempts':a,'makes':made}}
    p,c,detail=m.mix_shooting(rot,states,a*3,made*3,720,a*2,made*2,True)
    q,_,_=m.mix_shooting(rot,states,a*3,made*3,720,a*2,made*2,False)
    assert np.allclose(p,[.5,.5]) and np.allclose(q,p) and c==pytest.approx(.5)
    assert all(sum(v['zone_weights'])==pytest.approx(1.) for v in detail)

def test_score_tie_and_source_window():
    assert m.prior.probability([[30,12,15],[30,12,15]])==pytest.approx(.5,abs=1e-10)
    d=date(2026,1,10);items=[{'day':(d-timedelta(days=n)).isoformat()} for n in (366,365,4,3)]
    assert m.prior.window(items,d.isoformat())==items[1:3]

def test_minutes_precision_and_invalid_source():
    assert m.player_minutes('31:30')==(31.5,1/60)
    assert m.player_minutes('31')==(31.,1.)
    assert m.rotation_box({'id':'missing','summary_invalid':True}) is None

def test_future_shots_and_target_result_do_not_change_player_features():
    from copy import deepcopy
    a=[10,8,6,4,0,5,8,6,0];made=[5,4,3,2,0,2,3,2,0]
    side={'estimated_possessions':90.,'attempts':a,'makes':made,'fta':20,'ftm':15}
    games=[{'game_id':str(i),'day':f'2025-01-{i+1:02}','teams':['BOS','LAL'],'sides':[deepcopy(side),deepcopy(side)]} for i in range(6)]
    group={}
    for g in games:
        for team in g['teams']:
            group[(g['game_id'],team)]=[{'athlete_id':team,'minutes_seconds_displayed':14400,'accounting_ok':True,'hard_attempts':a,'hard_makes':made}]
    target={'day':'2025-01-20','teams':['BOS','LAL'],'scores':[90,80]};rots=[{'minutes':{'BOS':240}},{'minutes':{'LAL':240}}]
    state=m.build_day_state(target['day'],games,games,group);baseline,reason=m.calculate_match(target,state,rots);assert reason is None
    future=deepcopy(games[0]);future['game_id']='future';future['day']=target['day'];future['sides'][0]['makes']=[0]*9
    changed=dict(target,scores=[999,0],actual_minutes={'new':500});later=m.build_day_state(target['day'],games+[future],games+[future],group)
    after,reason=m.calculate_match(changed,later,rots)
    assert reason is None and baseline==after
