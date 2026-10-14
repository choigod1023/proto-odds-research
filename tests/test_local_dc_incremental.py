from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.optimize import check_grad
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import local_dc_incremental as dc


def rows():
    output = []
    for i in range(36):
        hg, ag = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (0, 3)][i % 6]
        output.append(dict(match_id=f'D1:{i}', league='D1', season=19,
                           date=(date(2019, 1, 1)+timedelta(days=i*8)).isoformat(),
                           home=['A', 'B', 'C'][i % 3], away=['B', 'C', 'A'][i % 3],
                           hg=hg, ag=ag, y=0 if hg > ag else 1 if hg == ag else 2,
                           prices=[1.8, 3.2, 4.]))
    return output


@pytest.mark.parametrize('sign', [-1, 1])
@pytest.mark.parametrize('half_life', [None, 180])
def test_joint_gradient_includes_dependent_feasible_bound(sign, half_life):
    objective, teams, _ = dc.make_objective(rows(), half_life, True)
    theta = np.r_[.3, .25, [.1, -.2, .3], [-.15, .25, .05], sign*.7]
    error = check_grad(lambda x: objective(x)[0], lambda x: objective(x)[1], theta)
    assert error < 2e-4


def test_independent_matches_prior_ridge10_and_zero_rho_predictions():
    config = dict(dc=False, half_life=None)
    model, audit = dc.fit_model(rows(), config)
    prior, _ = dc.base.fit_poisson(rows(), 10)
    actual, info = dc.predict(model, rows())
    expected, _ = dc.base.predict_poisson(prior, rows())
    assert np.allclose(actual, expected, atol=1e-9)
    assert audit['rho'] == 0 and info['min_tau'] == 1
    dc_model = dict(model, dc=True, theta=np.r_[model['theta'], 0.])
    assert np.allclose(dc.predict(dc_model, rows())[0], actual)


def test_valid_tau_for_extreme_pairings_and_unseen_teams():
    for z in (-4., 4.):
        model = dict(n=3, teams={'A': 0, 'B': 1, 'C': 2}, dc=True,
                     theta=np.r_[.2, .3, [1.5, -1.2, .3], [-1.1, .7, .2], z])
        candidates = [dict(home=h, away=a) for h in ('A', 'B', 'C', 'unknown') for a in ('A', 'B', 'C', 'unknown')]
        p, audit = dc.predict(model, candidates)
        dc.base.validate(p)
        assert audit['min_tau'] > dc.EPS
        assert audit['maximum_mass_error'] < 3e-12


def test_decay_likelihood_normalization_is_constant_across_half_lives():
    sample = rows()
    for half_life in (None, 180, 365):
        w = dc.weights(sample, half_life)
        assert np.isclose(w.sum(), len(sample))
        if half_life:
            assert w[-1] > w[0]
    # Identical dated observations get equal normalized weights at any half-life.
    same_day = [dict(r, date=sample[0]['date']) for r in sample]
    theta = np.zeros(8)
    flat = dc.make_objective(same_day, None, False)[0](theta)
    decayed = dc.make_objective(same_day, 180, False)[0](theta)
    assert flat[0] == decayed[0] and np.array_equal(flat[1], decayed[1])


@pytest.mark.parametrize('raw_rho', [-.7, 0., .7])
def test_cdf_hda_matches_small_joint_tensor(raw_rho):
    model = dict(n=2, teams={'A': 0, 'B': 1}, dc=True,
                 theta=np.r_[.2, .25, [.15, -.15], [-.1, .1], raw_rho])
    sample = [dict(home='A', away='B'), dict(home='B', away='A'), dict(home='new', away='A')]
    actual, audit = dc.predict(model, sample)
    means = dc.prediction_rates(model, sample)
    rho = dc.rho_transform(model['theta'], model['n'], True)[0]
    grid = np.arange(audit['score_support']+1)
    expected = []
    for mh, ma in means:
        joint = np.outer(poisson.pmf(grid, mh), poisson.pmf(grid, ma))
        for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)):
            joint[i, j] *= dc.dixon_coles_tau(i, j, mh, ma, rho)
        expected.append(np.array([np.tril(joint, -1).sum(), joint.trace(), np.triu(joint, 1).sum()])/joint.sum())
    assert np.allclose(actual, expected, atol=1e-14, rtol=0)


def test_future_outcome_mutation_and_seven_day_fit_exclusion():
    training = rows()
    last = date.fromisoformat(training[-1]['date'])
    too_recent = dict(training[-1], match_id='recent', date=(last+timedelta(days=2)).isoformat())
    evaluation = [dict(training[-1], match_id='future', season=20, date=(last+timedelta(days=7)).isoformat())]
    guarded = dc.base.guarded_training(training+[too_recent]+evaluation, 19, evaluation)
    assert too_recent not in guarded
    model, _ = dc.fit_model(guarded, dict(dc=True, half_life=180))
    mutated = deepcopy(evaluation)
    mutated[0].update(hg=99, ag=77, y=2)
    assert np.array_equal(dc.predict(model, evaluation)[0], dc.predict(model, mutated)[0])


def test_missing_price_day_and_distinct_ticket_ledger():
    actual = rows()[:3]
    actual[1]['date'] = actual[0]['date']
    actual[2]['prices'] = None
    tickets = dc.base.choose_tickets(actual[:2], np.array([[.7, .2, .1]]*2), 'highestprob')
    ledger = dc.base.settle(actual, tickets)
    assert len(ledger) == 2 and ledger[-1]['stake'] == 0
    assert len({leg['match_id'] for leg in ledger[0]['legs']}) == 2
    assert sum(r['budget'] for r in ledger) == 2


def test_protocol_guard_mocked_git_and_lf_normalized(monkeypatch):
    body = dc.PROTOCOL.read_bytes()
    monkeypatch.setattr(dc.subprocess, 'check_output',
                        lambda command, **kwargs: 'mock-commit\n' if 'rev-parse' in command else body.replace(b'\r\n', b'\n'))
    assert dc.verify_protocol() == 'mock-commit'
    monkeypatch.setattr(dc.subprocess, 'check_output', lambda *args, **kwargs: b'changed')
    with pytest.raises(ValueError, match='Frozen DC'):
        dc.verify_protocol()


def test_final_artifact_hashes_and_trial_counts():
    path = dc.ROOT/'docs/research/2026-10-01-dc-results.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['trial_count'] == len(result['trials']) == 60
    assert result['actual_league_fits'] == 40
    assert result['protocol_sha256_lf'] == hashlib.sha256(dc.lf(dc.PROTOCOL.read_bytes())).hexdigest()
    for relative, digest in result['code_sha256_lf'].items():
        assert '\\' not in relative
        assert hashlib.sha256(dc.lf((dc.ROOT/relative).read_bytes())).hexdigest() == digest
