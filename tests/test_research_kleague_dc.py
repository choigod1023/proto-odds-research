from copy import deepcopy
from datetime import date, timedelta
import numpy as np
import pytest
from scipy.optimize._numdiff import approx_derivative
from scipy.stats import skellam

from src.research_kleague_dc import objective, fit, joint, predict


def history():
    return [dict(id=str(i), day=(date(2023,1,1)+timedelta(days=i)).isoformat(),
                 home_id='A' if i%2 else 'B', away_id='B' if i%2 else 'A', hg=i%4, ag=i%3)
            for i in range(240)]


def test_analytic_gradient_matches_finite_difference_including_low_scores():
    h=np.array([0,0,1,1,0,1]);a=1-h;hg=np.array([0,0,1,1,4,2]);ag=np.array([0,1,0,1,2,3])
    b=np.array([.1,.2,.1,-.1,.05,-.05,-.08]);w=np.array([1,.9,.8,.7,.6,.5])
    numeric=approx_derivative(lambda x:objective(x,h,a,hg,ag,w,2)[0],b,method='3-point').ravel()
    actual=objective(b,h,a,hg,ag,w,2)[1]
    assert np.max(np.abs(numeric-actual))<1e-7


@pytest.mark.parametrize('lh,la,rho',[(.05,.05,-.15),(6,6,.025),(6,6,-.15),(1.5,1.1,0),(1.5,1.1,-.1)])
def test_joint_is_positive_normalized_and_preserves_poisson_means(lh,la,rho):
    p=joint(lh,la,rho);g=np.arange(31)
    assert np.min(p)>=0 and abs(p.sum()-1)<1e-12
    assert abs((p.sum(1)*g).sum()-lh)<1e-9
    assert abs((p.sum(0)*g).sum()-la)<1e-9


def test_independent_case_agrees_with_separate_skellam_cdf():
    p=joint(1.6,1.2,0)
    actual=np.array([np.tril(p,-1).sum(),np.trace(p),np.triu(p,1).sum()])
    expected=[1-skellam.cdf(0,1.6,1.2),skellam.pmf(0,1.6,1.2),skellam.cdf(-1,1.6,1.2)]
    assert np.max(np.abs(actual-expected))<1e-12
    assert np.trace(joint(1.6,1.2,-.1))>np.trace(p)


def test_training_uses_only_past_and_unknown_team_is_zero_prior():
    rows=history();before=deepcopy(rows)
    s=fit(rows,'2024-01-01')
    assert s['converged'] and abs(sum(s['attack']))<1e-12 and abs(sum(s['defence']))<1e-12
    p,info=predict(s,'new','A')
    assert info['unknown_teams']==['new'] and abs(sum(p)-1)<1e-12
    assert rows==before
    with pytest.raises(ValueError,match='embargoed'):
        fit(rows+[dict(id='future',day='2024-01-01',home_id='A',away_id='B',hg=9,ag=0)],'2024-01-01')
    with pytest.raises(ValueError,match='200 unique'):
        fit(rows[:100],'2024-01-01')
    with pytest.raises(ValueError,match='200 unique'):
        fit(rows+[rows[0]],'2024-01-01')
