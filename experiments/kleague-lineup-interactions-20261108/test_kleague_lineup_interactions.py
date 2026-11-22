import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec=importlib.util.spec_from_file_location('kleague_xi_interactions',Path(__file__).with_name('run.py'))
model=importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def game(day='2026-01-01',ident='a'):
    return {'id':ident,'source_game_id':ident,'day':day,'home':'서울','away':'전북',
            'home_xi':[str(i) for i in range(11)],'away_xi':[str(i+20) for i in range(11)],
            'home_projected':[str(i) for i in range(11)],'away_projected':[str(i+20) for i in range(11)],
            'home_score':1,'away_score':0,'market_p':[.4,.3,.3],'odds':[2.4,3.1,2.5],'y':0}


def test_four_day_boundary_and_future_xi_invariance():
    old=game(); current=game('2026-01-05','b');future=game('2026-01-06','c')
    before=model.projected([old,current,future])
    assert before[1]['home_prior']['day']=='2026-01-01'
    assert not model.projected([old,game('2026-01-04','early')])[1]['home_projected']
    altered=copy.deepcopy(current);altered['home_xi']=['new'+str(i) for i in range(11)]
    assert model.projected([old,altered,future])[1]['home_projected']==before[1]['home_projected']


def test_no_heldout_stats_or_current_xi_in_projected_features():
    r=game();z=copy.deepcopy(r);z['home_score']=99;z['y']=2
    z['home_xi']=['new'+str(i) for i in range(11)]
    assert model.terms(r,'home','pair')==model.terms(z,'home','pair')
    assert model.terms(r,'home','pair',oracle=True)!=model.terms(z,'home','pair',oracle=True)


def test_poisson_score_orientation_and_unseen_player_safety():
    r=game();x,vocab=model.design([r],'team')
    beta=np.zeros(len(vocab));beta[vocab['home_effect']]=np.log(3)
    p,means=model.forecast([r],'team',(beta,vocab))
    assert np.allclose(means,[[3,1]])
    assert p[0,0]>p[0,2] and p[0,1]>0 and np.allclose(p.sum(axis=1),1)
    x,vocab=model.design([r],'pair')
    beta=np.zeros((len(vocab),2));r['home_projected']=['unseen']*11
    q=model.forecast_market_offset([r],'pair',(beta,vocab))
    assert np.allclose(q,[r['market_p']])


def test_reject_duplicate_player_ids():
    g={'data':{'home':{'players':[{'playerId':'same','positionOrder':i} for i in range(1,12)]}}}
    with pytest.raises(ValueError):model.starting_xi(g,'home')
