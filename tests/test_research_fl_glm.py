"""Synthetic and 2024-only checks; never score 2025 outcomes or ROI."""
from copy import deepcopy
from datetime import date, timedelta
from itertools import islice
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize._numdiff import approx_derivative

from src.devig import shin
from src.research_fl_glm import fit, predict, loss_gradient_curvature


def rows():
    # All labels are synthetic and give a finite interior positive exponent.
    return [{'id': f'g{i}', 'day': (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
             'prices': [2.0, 3.0, 4.0], 'y': 0 if i < 66 else 1 if i < 102 else 2}
            for i in range(120)]


def test_gradient_and_curvature_against_numerical_derivatives():
    x = np.log(np.array([[1.3, 4., 9.], [2.2, 3.1, 3.4], [5., 2., 3.]]))
    y = np.array([0, 2, 1])
    for beta in (-3., 0., 1., 7.):
        loss, grad, curvature = loss_gradient_curvature(beta, x, y)
        numerical_gradient = approx_derivative(lambda z: loss_gradient_curvature(float(z[0]), x, y)[0], [beta])[0]
        numerical_curvature = approx_derivative(lambda z: loss_gradient_curvature(float(z[0]), x, y)[1], [beta])[0]
        assert np.isfinite(loss) and abs(grad - numerical_gradient) < 1e-8
        assert curvature >= 0 and abs(curvature - numerical_curvature) < 1e-8


def test_fit_interior_root_and_immutability():
    training = rows()
    original = deepcopy(training)
    state = fit(training, '2025-01-01')
    assert state['training_rows'] == 120 and state['cutoff_inclusive'] == '2024-12-25'
    assert 0 < state['beta'] < 10 and abs(state['gradient']) < 1e-9
    assert state['curvature'] > 0 and training == original
    assert json.loads(json.dumps(state)) == state


def test_embargo_identity_and_outcome_validation():
    training = rows()
    with pytest.raises(ValueError, match='100'):
        fit(training[:99], '2025-01-01')
    with pytest.raises(ValueError, match='unique'):
        fit(training + [training[0]], '2025-01-01')
    with pytest.raises(ValueError, match='embargoed'):
        fit(training + [{**training[0], 'id': 'late', 'day': '2024-12-26'}], '2025-01-01')
    fit(training + [{**training[0], 'id': 'edge', 'day': '2024-12-25'}], '2025-01-01')
    for invalid in (True, -1, 3, 0.5, None):
        with pytest.raises(ValueError, match='outcome'):
            fit([{**training[0], 'y': invalid}, *training[1:]], '2025-01-01')
    with pytest.raises(ValueError, match='ISO'):
        fit(training, '2025-02-30')


def test_boundary_or_flat_objective_stops():
    training = rows()
    # All favorites win: NLL decreases throughout the specified beta interval.
    with pytest.raises(ValueError, match='bound'):
        fit([{**r, 'y': 0} for r in training], '2025-01-01')
    # All longshots win: the minimum is at the opposite boundary.
    with pytest.raises(ValueError, match='bound'):
        fit([{**r, 'y': 2} for r in training], '2025-01-01')
    with pytest.raises(ValueError, match='identifiable'):
        fit([{**r, 'prices': [2, 2, 2]} for r in training], '2025-01-01')


def test_ranking_matches_shin_when_beta_positive_and_ties_are_preserved():
    for prices in ([1.4, 3.8, 7.5], [2.2, 2.2, 3.4], [4.5, 3.4, 1.6]):
        for beta in (.05, 1., 4.):
            p, detail = predict({'beta': beta}, {'prices': prices})
            assert detail['beta'] == beta and abs(sum(p) - 1) < 1e-14
            assert set(np.flatnonzero(np.isclose(p, max(p), rtol=0, atol=1e-12))) == set(
                np.flatnonzero(np.isclose(shin(list(prices)), max(shin(list(prices))), rtol=0, atol=1e-12)))
    p, _ = predict({'beta': 0.}, {'prices': [1.5, 3., 5.]})
    assert p == pytest.approx([1/3]*3)  # downstream 1e-12 tie rule abstains
    p, _ = predict({'beta': -1.}, {'prices': [1.5, 3., 5.]})
    assert int(np.argmax(p)) == 2  # negative beta does not preserve price ordering


def test_invalid_prices_states_and_outcome_poisoning():
    state = fit(rows(), '2025-01-01')
    original = {'prices': [2.0, 3.0, 4.0], 'y': 0, 'hg': 2, 'ag': 1}
    poison = {'prices': [2.0, 3.0, 4.0], 'y': object(), 'hg': object(), 'ag': object()}
    assert predict(state, original) == predict(state, poison)
    for prices in ([2., 3.], [2., 3., 1.], [2., 3., float('nan')],
                   [2., 3., float('inf')], [2., 3., True], [2., 3., 'bad']):
        with pytest.raises(ValueError):
            predict(state, {'prices': prices})
        with pytest.raises(ValueError):
            fit([{**rows()[0], 'prices': prices}, *rows()[1:]], '2025-01-01')
    for beta in (None, float('nan'), -5., 10., True):
        with pytest.raises(ValueError):
            predict({'beta': beta}, original)
    with pytest.raises(ValueError, match='underflow'):
        predict({'beta': 9.}, {'prices': [1.1, 2., 1e308]})


def test_frozen_2024_meta_history_only():
    path = Path(__file__).resolve().parents[1] / 'experiments/kleague-three-candidates-20261101/oof.jsonl'
    with path.open(encoding='utf-8') as stream:
        history = [json.loads(line) for line in islice(stream, 228)]
    assert len(history) == 228 and all(r['split'] == 'meta_history_2024' for r in history)
    state = fit(history, '2025-01-01')
    assert state['training_rows'] == 228 and -5 < state['beta'] < 10
    p, _ = predict(state, {'prices': [1.8, 3.5, 4.5]})
    assert abs(sum(p)-1) < 1e-12
