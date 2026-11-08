import pytest
from scripts.check_nba_components_pgf import pmf, win_probability


def test_generating_function_distribution_has_theoretical_moments():
    p = pmf({1: .5, 2: 1.2, 3: .7})
    mean = sum(k*v for k, v in enumerate(p))
    variance = sum((k-mean)**2*v for k, v in enumerate(p))
    assert mean == pytest.approx(.5+2*1.2+3*.7)
    assert variance == pytest.approx(.5+4*1.2+9*.7)
    assert win_probability(p, p) == pytest.approx(.5)


def test_zero_jump_rate_is_a_degenerate_zero_score():
    zero = pmf({1: 0., 2: 0., 3: 0.})
    assert zero[0] == 1 and sum(zero[1:]) == 0
    positive = pmf({1: 2., 2: 0., 3: 0.})
    assert win_probability(positive, zero) == pytest.approx(1-.5*positive[0])


def test_truncated_mass_is_rejected_instead_of_rescaling():
    with pytest.raises(ValueError, match="score_tail"):
        pmf({1: 100., 2: 100., 3: 100.}, maximum=2)
