import sys
from pathlib import Path
from copy import deepcopy
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nba_spatial_conserved_model as m

def sample_side():
    return {'estimated_possessions':90.,'attempts':[20,10,8,5,0,5,20,10,0],'makes':[12,5,4,2,0,2,7,3,0],'fta':20,'ftm':15,'orb':9,'tov':10}
def source():
    return [{'game_id':str(i),'day':f'2025-01-{i+1:02}','date_utc':f'2025-01-{i+1:02}T00:00Z','teams':['BOS','LAL'],'sides':[sample_side(),sample_side()]} for i in range(10)]

def test_attempt_rate_conserved_and_missing_zone_zero():
    v=m.prior.arr(sample_side()); detail=m.family_details(v*10,v*10,v*20)
    for item in detail:
        assert sum(item['weights'])==pytest.approx(1.)
        assert sum(item['family_rate']*item['weights'])==pytest.approx(item['family_rate'])
    assert detail[0]['weights'][-1]==0

def test_equal_side_tie_and_causality():
    h=source();target={'day':'2025-01-20','teams':['BOS','LAL'],'scores':[90,80]}
    a=m.candidate(target,h); assert a['probability']==pytest.approx(.5,abs=1e-10)
    future=deepcopy(h);future.append(dict(deepcopy(h[0]),game_id='future',day='2025-01-20'))
    b=m.candidate(dict(target,scores=[999,0]),future);assert a==b

def test_soft_pool_family_totals_and_identity():
    h=source();f=deepcopy(h)
    a=f[0]['sides'][0]['attempts'];a[0]-=.25;a[1]+=.25
    orig=m.validate_pools
    # Expand without duplicate IDs to exercise pool-level fail-closed check.
    for i in range(10,1185):
        item=deepcopy(h[i%10]);item['game_id']=str(i);h.append(item);f.append(deepcopy(item))
    orig(h,f)
    f[0]['sides'][0]['attempts'][0]+=.1
    with pytest.raises(ValueError,match='family shot totals changed'):orig(h,f)

def test_no_prior_and_beta():
    with pytest.raises(ValueError):m.family_details(np.zeros(21),np.zeros(21),np.zeros(21))
    rows=[{'market':.6,'feature_delta':.2,'home_win':1},{'market':.6,'feature_delta':-.2,'home_win':0}]
    assert m.prior.fit_beta(rows)==m.prior.fit_beta(rows)

def test_calendar_365_and_4():
    from datetime import date,timedelta
    d=date(2026,1,10);values=[{'day':(d-timedelta(days=n)).isoformat()} for n in (366,365,4,3)]
    assert m.prior.window(values,d.isoformat())==values[1:3]
