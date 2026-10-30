from copy import deepcopy
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import check_grad

from scripts.kleague_decay_backtest import (
    decay_weights, projected_features, tau_and_derivatives, design, dc_objective,fit,predict,score,key,
)


def game(day='2024-01-01',home='a',away='b',hg=1,ag=0):
    return dict(league='K리그1',date=day,home=home,away=away,hg=hg,ag=ag,
        home_players=[dict(pid=f'{home}{i}',ga=float(i==1)) for i in range(11)],
        away_players=[dict(pid=f'{away}{i}',ga=float(i==2)) for i in range(11)])


def test_decay_means_90_day_old_game_has_half_weight_and_no_future_rows():
    assert decay_weights(['2024-01-01'],'2024-03-31',90)[0]==pytest.approx(.5)
    assert decay_weights(['2024-01-01'],'2024-03-31',None)[0]==1
    with pytest.raises(ValueError,match='strictly before'):
        decay_weights(['2024-03-31'],'2024-03-31',90)


def test_current_lineup_and_score_cannot_change_current_projected_features():
    history=[game(),game('2024-01-08'),game('2024-01-15')]
    before=projected_features(history)
    changed=deepcopy(history)
    changed[-1]['home_players']=[dict(pid=f'future{i}',ga=1000.) for i in range(11)]
    changed[-1]['hg']=99
    after=projected_features(changed)
    assert before[key(history[-1])]==after[key(history[-1])]
    assert before[key(history[0])]['values']==[0,0]
    assert before[key(history[-1])]['latest_source_date']=='2024-01-08'


def test_same_day_order_does_not_leak_other_fixtures_or_player_points():
    history=[game(),game('2024-01-08'),game('2024-01-08',home='a',away='c',hg=9)]
    forward=projected_features(history)
    reverse=projected_features(list(reversed(history)))
    assert forward==reverse


def test_stale_or_incomplete_lineup_falls_back_and_is_flagged():
    early=game();late=game('2024-04-01')
    f=projected_features([early,late])[key(late)]
    assert f['values']==[0,0] and f['available']==[False,False]


def test_all_four_low_scores_have_their_own_correction():
    hg=np.array([0,0,1,1,2]);ag=np.array([0,1,0,1,3])
    tau,*_=tau_and_derivatives(hg,ag,np.full(5,1.2),np.full(5,.8),-.04)
    assert tau==pytest.approx([1+.04*.96,1-.04*1.2,1-.04*.8,1+.04,1])


def test_joint_likelihood_analytic_gradient_matches_numerical_gradient():
    rows=[game(hg=h,ag=a) for h,a in [(0,0),(0,1),(1,0),(1,1),(3,2)]]
    features=projected_features(rows)
    xh,xa=design(rows,['a','b'],features,True)
    theta=np.array([.2,.1,.1,-.1,.1,-.1,.2,-.02]);penalty=np.array([0,0,.02,.02,.02,.02,.1])
    def objective(v):return dc_objective(v,xh,xa,np.array([r['hg'] for r in rows]),
        np.array([r['ag'] for r in rows]),np.full(len(rows),1/len(rows)),penalty)
    error=check_grad(lambda v:objective(v)[0],lambda v:objective(v)[1],theta)
    assert error<1e-5


def test_predict_probabilities_sum_to_one_and_home_advantage_has_right_direction():
    rows=[game()];features=projected_features(rows)
    model=dict(teams=['a','b'],beta=np.array([0.,.5,0,0,0,0]),rho=-.04)
    p=predict(model,rows,features,False)
    assert p.shape==(1,3) and p.sum()==pytest.approx(1)
    assert p[0,0]>p[0,2] and np.all(p>0)


def test_unlimited_flat_stake_counts_every_fixture_and_draw_is_a_real_outcome():
    rows=[game(hg=2,ag=1),game(hg=1,ag=1),game(hg=0,ag=1)]
    p=np.array([[.6,.2,.2],[.2,.6,.2],[.6,.2,.2]])
    stats=score(rows,p,np.array([[2.,3.,4.]]*3))
    assert stats['n']==3 and stats['wins']==2 and stats['roi']==pytest.approx(2/3)
    assert stats['net_per_10000_krw']==20000


def test_fitting_filters_future_scores_and_preserves_effective_sample_size():
    rows=[game(f'2024-01-{i:02d}',hg=i%3,ag=(i+1)%3) for i in range(1,32)]
    features=projected_features(rows)
    a=fit(rows,features,'2024-02-01',None,False)
    altered=rows+[game('2024-03-01',hg=99,ag=0)]
    b=fit(altered,projected_features(altered),'2024-02-01',None,False)
    assert np.allclose(a['beta'],b['beta'])
    assert a['n']==a['effective_n']==31 and a['latest_training_date']=='2024-01-31'


def test_recorded_experiment_resettles_and_uses_only_past_validation_for_selection():
    from scripts.verify_kleague_backtest import verify
    folder=Path(__file__).resolve().parents[1]/'experiments/kleague-decay-20261029'
    checked=verify(folder)
    assert checked['fixtures']==30 and checked['models']==22
    assert checked['independent_flat_stake_settlement']
    assert not checked['monthly_loss_cap_applied']
