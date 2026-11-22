from copy import deepcopy
import pytest
from scripts.audit_nba_spatial_structure import (
    beta_root, context, family_components, metrics, reference, score_probability,
    sigmoid, sums, soft_weights,
)


def game(day, identifier):
    side = dict(attempts=[20,10,8,4,0,10,20,8,0], makes=[12,4,3,1,0,4,7,2,0],
                estimated_possessions=90.8, fta=20, ftm=15)
    return dict(game_id=identifier, day=day, date_utc=day+'T12:00:00Z',
                teams=['BOS','LAL'], sides=[deepcopy(side), deepcopy(side)])


def test_attempt_mismatch_and_exact_symmetric_decomposition():
    # Large exposure makes smoothing small enough to expose the mathematical difference.
    own = [100000,80000,20000,0,0,0,80000,20000,0,0,
           56000,6000,0,0,0,32000,6000,0,0,20000,15000]
    league = [100000,50000,50000,0,0,0,50000,50000,0,0,
              30000,20000,0,0,0,20000,15000,0,0,20000,15000]
    components, _ = family_components(own, own, league)
    assert components[0]['rate_ratio'] > 1.35
    for family in components:
        assert family['volume_effect']+family['accuracy_effect'] == pytest.approx(family['s_made']-family['q_made'],abs=1e-14)
        assert family['conservation_error'] == pytest.approx(0,abs=1e-14)
        assert family['conserved_made']/family['q_rate'] == pytest.approx(family['s_accuracy'])


def test_conservation_is_identity_when_zone_profiles_equal_league():
    vector = sums(game('2025-01-01','a')['sides'])
    components, _ = family_components(vector,vector,vector)
    for family in components:
        assert family['rate_ratio'] == pytest.approx(1.)
        assert family['q_made'] == pytest.approx(family['conserved_made'])


def test_window_preserves_bounds_and_rejects_target_outcome():
    pool = [game('2025-01-01','start'),game('2025-12-28','end')]
    pool += [game(f'2025-10-{i:02d}',str(i)) for i in range(1,6)]
    target = dict(day='2026-01-01',teams=['BOS','LAL'])
    baseline = reference(pool,target)
    assert baseline['asof_prior_games'] == 7
    poisoned = dict(day='2026-01-01',game_id='future',date_utc='irrelevant',sides=[],teams=['BOS','LAL'])
    poisoned_recent = dict(poisoned,day='2025-12-29')
    assert reference(pool+[poisoned,poisoned_recent],target) == baseline
    with pytest.raises(ValueError,match='insufficient_history'):
        context(pool[:1],target)


def test_pgf_probability_symmetry_and_shift():
    assert score_probability([[12,8,15],[12,8,15]]) == pytest.approx(.5,abs=1e-12)
    p = score_probability([[14,8,15],[12,8,15]])
    assert p > .5
    assert score_probability([[12,8,15],[14,8,15]]) == pytest.approx(1-p,abs=1e-12)


def test_independent_offset_gradient_and_price_payout():
    rows = [dict(market=.5,nonspatial=.5,conserved=sigmoid(2),home_win=1)]
    beta = beta_root(rows,'conserved')
    assert beta > 0
    assert 2*(sigmoid(2*beta)-1)+10*beta == pytest.approx(0,abs=1e-12)
    games = [dict(day='2025-01-01',home_win=1,odds=[1.8,2.1],probabilities={'h':.6}),
             dict(day='2025-01-01',home_win=0,odds=[1.8,2.1],probabilities={'h':.6})]
    assert metrics(games,'h')['roi'] == -.1
    assert metrics(games,'h')['daily_mdd'] == .2


def test_soft_boundary_memberships_conserve_attempt_and_family():
    for points, boundary in ((2,4),(2,10),(2,16),(3,24),(3,27)):
        pairs = soft_weights(points,25,boundary)
        assert sum(w for _,w in pairs) == pytest.approx(1.)
        assert sum(w == .5 for _,w in pairs) == 2
    assert soft_weights(2,None,None) == [(4,1.)]
    assert soft_weights(3,float('nan'),0) == [(8,1.)]
    assert sum(w for _,w in soft_weights(2,25,0)) == 1.
