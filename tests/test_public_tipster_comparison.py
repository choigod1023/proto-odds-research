import numpy as np
import pandas as pd
import pytest
from public_tipster_comparison import augment, metrics, match_records


def test_signal_normalizes_and_zero_is_identity():
    p=np.array([[.4,.35,.25],[.6,.1,.3]])
    assert np.allclose(augment(p,[2,0],0),p)
    assert np.allclose(augment(p,[2,0]).sum(1),1)
    assert augment(p,[2,0])[0,2] > p[0,2]
    assert np.array_equal(p,np.array([[.4,.35,.25],[.6,.1,.3]]))


def test_invalid_choice():
    with pytest.raises(ValueError): augment([[.5,.5]],[2])


def test_roi_includes_returned_stake():
    r=metrics([0,1],[[2.,1.5],[3.,1.2]],[0,0])
    assert r['accuracy']==.5
    assert r['roi']==0
    assert metrics([],[],[])['roi'] is None


def test_match_rejects_ambiguity_and_conflict():
    d=pd.DataFrame([dict(date=pd.Timestamp('2025-01-01'),sport='bs',home_team='KT',away_team='LG',kickoff='2025-01-01 18:00')])
    p=pd.DataFrame([dict(source='attv24',id=1,date='2025-01-01',sport='bs',home='KT',away='LG',choice='away',conflict=False)])
    joined,audit=match_records(p,d)
    assert len(joined)==1 and not joined[0]['strict_eligible']
    assert joined[0]['choice_index']==1
    assert not match_records(p,pd.concat([d,d],ignore_index=True))[0]
    p['conflict']=True
    assert not match_records(p,d)[0]


def test_duplicate_votes_do_not_duplicate_events():
    d=pd.DataFrame([dict(date=pd.Timestamp('2025-01-01'),sport='sc',home_team='풀럼',away_team='아스널',kickoff='2025-01-01 18:00')])
    p=pd.DataFrame([dict(source='attv24',id=1,date='2025-01-01',sport='sc',home='풀럼',away='아스널',choice='away',conflict=False)])
    assert len(match_records(pd.concat([p,p]),d)[0])==1
    p2=p.copy();p2['choice']='home'
    assert not match_records(pd.concat([p,p2]),d)[0]
