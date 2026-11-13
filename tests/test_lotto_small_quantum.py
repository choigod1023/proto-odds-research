import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto_small_quantum import BASE, COUNTS, INITIAL, statevector, probabilities, evaluate


def test_circuit_against_independent_dense_matrices():
    theta = np.linspace(-1.3, 2.1, 12)
    state = np.zeros(64); state[0] = 1
    for layer in range(2):
        for q in range(6):
            angle = theta[6*layer+q]/2
            ry = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
            operator = np.array([[1.]])
            for bit in reversed(range(6)):
                operator = np.kron(operator, ry if bit == q else np.eye(2))
            state = operator @ state
        for q in range(6):
            operator = np.zeros((64,64))
            for src in range(64):
                dst = src ^ (1 << ((q+1)%6)) if src & (1 << q) else src
                operator[dst,src] = 1
            state = operator @ state
    np.testing.assert_allclose(statevector(theta), state, atol=1e-14)
    np.testing.assert_allclose(np.dot(state,state),1,atol=1e-14)


def test_exact_uniform_combinatorial_marginals():
    assert COUNTS.sum() == math.comb(45,6)
    grid = COUNTS.reshape(8,8)
    for k in range(7):
        assert grid[k,:].sum() == math.comb(22,k)*math.comb(23,6-k)
        assert grid[:,k].sum() == math.comb(23,k)*math.comb(22,6-k)
    np.testing.assert_allclose(probabilities(INITIAL),BASE,atol=1e-14)


def test_full_combination_distribution_normalized():
    p = probabilities(np.linspace(.1,2.3,12))
    valid = COUNTS>0
    assert np.all(p[~valid] == 0)
    assert np.all(p[valid]>0)
    assert abs(np.sum(COUNTS[valid]*(p[valid]/COUNTS[valid]))-1)<1e-14


def test_holdout_does_not_affect_fit():
    rng = np.random.default_rng(77)
    a = rng.choice(64,1245,p=BASE); b=a.copy()
    b[740:]=rng.choice(64,505,p=BASE)
    first, second = evaluate(a,True), evaluate(b,True)
    assert first["fit"] == second["fit"]
    assert first["category_probabilities"] == second["category_probabilities"]
