from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import local_score_model_tournament as m


def row(i, day, y=0, league='D1', home='A', away='B', prices=None):
    return dict(match_id=f'{league}:{i}', date=day, league=league, season=19,
                home=home, away=away, y=y, hg=(2, 1, 0)[y], ag=(0, 1, 2)[y],
                prices=prices or [1.4, 3., 5.])


def training():
    return [row(i, (date(2019, 1, 1)+timedelta(days=8*i)).isoformat(), i % 3,
                home=('A', 'B', 'C')[i % 3], away=('B', 'C', 'A')[i % 3]) for i in range(30)]


def test_probabilities_fit_and_blends_normalize():
    rows = training()
    features = m.elo_features(rows, 20)
    beta, audit = m.fit_logistic(rows, features, 1)
    elo = m.predict_logistic(beta, rows, features)
    poisson, audit2 = m.fit_poisson(rows, 1)
    scores, clipped = m.predict_poisson(poisson, rows)
    assert audit['iterations'] > 0 and audit2['iterations'] > 0 and clipped == 0
    baseline = np.array([m.shin(r['prices']) for r in rows])
    for prediction in (elo, scores):
        for weight in (0, .25, .5, .75, 1):
            m.validate((1-weight)*prediction+weight*baseline)


def test_future_same_day_and_six_day_mutations_do_not_change_earlier_features():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-02'),
            row(2, '2020-01-08'), row(3, '2020-01-08', home='C'), row(4, '2020-01-09')]
    old = m.elo_features(rows, 20)
    mutated = deepcopy(rows)
    for r in mutated[1:]:
        r.update(y=2, hg=0, ag=20)
    new = m.elo_features(mutated, 20)
    for r in rows[:4]:
        assert new[r['match_id']] == old[r['match_id']]
    assert new[rows[4]['match_id']] != old[rows[4]['match_id']]
    assert old[rows[1]['match_id']][1] == 0
    assert old[rows[2]['match_id']][1] > 0  # Exactly seven days is available.


def test_rating_leagues_are_isolated_and_new_teams_have_prior():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-08', league='SP1'),
            row(2, '2020-01-08', home='New', away='Other')]
    features = m.elo_features(rows, 40)
    assert features[rows[1]['match_id']] == [1, 0, 0]
    assert features[rows[2]['match_id']] == [1, 0, 0]
    model, _ = m.fit_poisson(training(), 10)
    new = row(99, '2030-01-01', home='UnseenH', away='UnseenA')
    means, _ = m.poisson_means(model, [new])
    assert 'UnseenH' not in model['teams']
    assert np.allclose(means[0], [np.exp(model['theta'][0]+model['theta'][1]), np.exp(model['theta'][0])])


def test_score_grid_equal_means_symmetry():
    model = {'n': 1, 'teams': {'A': 0}, 'theta': np.array([np.log(1.2), 0, 0, 0])}
    probabilities, _ = m.predict_poisson(model, [row(1, '2020-01-01')])
    assert np.isclose(probabilities[0, 0], probabilities[0, 2])


def test_policies_all_outcomes_low_odds_strict_upper_boundary():
    rows = [row(0, '2020-01-01', prices=[2.2, 1.4, 5]), row(1, '2020-01-01', prices=[2.2, 1.4, 5])]
    p = np.array([[.7, .2, .1]]*2)
    assert m.choose_tickets(rows, p, 'highestprob')['D1', '2020-01-01']
    assert not m.choose_tickets(rows, p, 'p60range')['D1', '2020-01-01']
    assert not m.choose_tickets(rows, p, 'p60low')['D1', '2020-01-01']
    p = np.array([[.2, .7, .1]]*2)
    legs = m.choose_tickets(rows, p, 'p60low')['D1', '2020-01-01']
    assert len(legs) == 2 and all(leg['outcome'] == 1 and leg['odds'] < 1.5 for leg in legs)
    assert not m.choose_tickets(rows, p, 'p60range')['D1', '2020-01-01']


def test_distinct_games_outcomes_cannot_leak_and_ledger_reconciles():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-01'), row(2, '2020-01-02')]
    p = np.array([[.7, .2, .1]]*3)
    tickets = m.choose_tickets(rows, p, 'highestprob')
    mutated = deepcopy(rows)
    for r in mutated:
        r.update(y=2, hg=0, ag=9)
    assert m.choose_tickets(mutated, p, 'highestprob') == tickets
    ledger = m.settle(rows, tickets)
    assert ledger[0]['stake'] == 1 and ledger[0]['won'] == 1
    assert np.isclose(ledger[0]['profit'], 1.4**2-1)
    assert ledger[1]['stake'] == ledger[1]['profit'] == 0
    assert len({leg['match_id'] for leg in ledger[0]['legs']}) == 2
    totals = m.betting_metrics(ledger)
    assert totals['tickets'] == 1 and totals['coverage'] == .5
    assert np.isclose(totals['budget_return']*totals['days'], totals['profit'])
    duplicate = deepcopy(tickets)
    duplicate['D1', '2020-01-01'][1] = duplicate['D1', '2020-01-01'][0]
    with pytest.raises(ValueError, match='distinct'):
        m.settle(rows, duplicate)


def test_bootstrap_paired_deterministic_and_empty_stake_safe():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-08')]
    ledger = m.settle(rows, {})
    ci = m.betting_ci(ledger, ledger)
    assert ci == m.betting_ci(ledger, ledger)
    assert ci['roi']['model']['valid_replicates'] == 0
    assert ci['budget_return']['minus_shin']['low'] == 0
    assert ci['budget_return']['minus_shin']['high'] == 0


def test_bad_probabilities_rejected():
    for bad in ([[0, .5, .5]], [[.2, .2, .2]], [[float('nan'), .3, .7]]):
        with pytest.raises(ValueError):
            m.validate(bad)


def test_missing_price_only_day_keeps_cash_budget():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-01'), row(2, '2020-01-02')]
    rows[2]['prices'] = None
    priced = [r for r in rows if r['prices'] is not None]
    tickets = m.choose_tickets(priced, np.array([[.7, .2, .1]]*2), 'highestprob')
    ledger = m.settle(rows, tickets)
    assert m.betting_metrics(ledger)['days'] == 2
    assert ledger[-1]['budget'] == 1 and ledger[-1]['stake'] == 0


def test_fit_guard_seven_days_and_invalid_fold_dates():
    rows = [row(0, '2020-07-01'), row(1, '2020-07-02'), row(2, '2020-07-08')]
    rows[-1]['season'] = 20
    assert [r['match_id'] for r in m.guarded_training(rows, 19, rows[-1:])] == [rows[0]['match_id']]
    rows[1]['date'] = '2020-07-08'
    with pytest.raises(ValueError, match='overlap'):
        m.guarded_training(rows, 19, rows[-1:])


def test_frozen_protocol_required_before_loading(monkeypatch, tmp_path):
    assert m.verify_protocol().startswith('626a3db7')
    original = m.PROTOCOL
    monkeypatch.setattr(Path, 'read_bytes', lambda path: b'changed' if path == original else b'')
    with pytest.raises(ValueError, match='Frozen protocol'):
        m.verify_protocol()
