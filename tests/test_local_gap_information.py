from copy import deepcopy
import json
from unittest.mock import patch

import numpy as np
import pytest
from scripts import local_gap_information as gap


def row(day, identity=None, league='D1', home='A', away='B'):
    return dict(match_id=identity or day, date=day, league=league, home=home, away=away,
                season=19, stats=dict(HS=10., AS=6., HST=4., AST=2.),
                prices=[1.5, 4., 6.], y=0, hg=2, ag=0)


def test_appendix_hand_calculation_simultaneous():
    h, a = gap.update(np.zeros(4), np.zeros(4), 10, 6, .2, .8)
    np.testing.assert_allclose(h, [1.6, .96, .4, .24])
    np.testing.assert_allclose(a, [.24, .4, .96, 1.6])
    assert np.all(gap.update(h, a, 0, 0, .2, .8)[0] >= 0)


def test_zero_initialization_and_seven_day_queue():
    rows = [row('2020-01-01'), row('2020-01-07'), row('2020-01-08')]
    f = gap.feature_rows(rows, .2, .8)
    np.testing.assert_array_equal(f[rows[0]['match_id']], np.zeros(4))
    np.testing.assert_array_equal(f[rows[1]['match_id']], np.zeros(4))
    np.testing.assert_allclose(f[rows[2]['match_id']], [1.6, .96, .64, .32])


def test_current_future_results_and_statistics_never_change_features():
    rows = [row('2020-01-01'), row('2020-01-08'), row('2020-01-09')]
    expected = gap.feature_rows(rows, .2, .8)
    mutated = deepcopy(rows)
    for r in mutated[1:]:
        r.update(y=2, hg=0, ag=20)
        r['stats'] = {k: 1000. for k in gap.KEYS}
    actual = gap.feature_rows(mutated, .2, .8)
    for r in rows:
        np.testing.assert_array_equal(expected[r['match_id']], actual[r['match_id']])


def test_league_isolation_and_missing_pair_skip():
    first = row('2020-01-01')
    first['stats']['HS'] = None
    rows = [first, row('2020-01-08'), row('2020-01-09', 'sp', league='SP1')]
    f = gap.feature_rows(rows, .2, .8)
    np.testing.assert_allclose(f['2020-01-08'], [0, 0, .64, .32])
    np.testing.assert_array_equal(f['sp'], np.zeros(4))


def test_duplicate_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        gap.feature_rows([row('2020-01-01')]*2, .1, .5)


def test_train_only_means_missing_not_zero():
    train = [row('2020-01-01'), row('2020-01-01', 'sp', league='SP1')]
    extra = row('2020-01-02')
    extra['stats']['HS'] = None
    means = gap.mean_statistics(train+[extra])
    assert means['D1'] == [10, 6, 4, 2]
    outer = row('2021-01-01')
    outer['stats']['HS'] = 1000
    assert gap.mean_statistics(train) == means


def test_reference_uses_inner_selected_not_outer_best():
    r = row('2020-01-01')
    saved = dict(r, probabilities=dict(inner_selected=[.5, .3, .2], no_schedule=[.7, .2, .1]))
    np.testing.assert_allclose(gap.reference_predictions([r], dict(forecasts=[saved])), [[.5, .3, .2]])
    saved['y'] = 1
    with pytest.raises(ValueError, match='source values'):
        gap.reference_predictions([r], dict(forecasts=[saved]))
    with pytest.raises(ValueError, match='identity'):
        gap.reference_predictions([r], dict(forecasts=[]))


def test_reused_gradient_and_zero_offset():
    x = np.array([[1., .2], [1., -.5], [1., .8]])
    m = np.array([[.5, .3, .2]]*3)
    y = np.array([0, 1, 2])
    b = np.linspace(-.1, .1, 6)
    _, grad = gap.info.objective(b, x, m, y, 1.)
    numerical = []
    for j in range(6):
        step = np.zeros(6)
        step[j] = 1e-6
        numerical.append((gap.info.objective(b+step, x, m, y, 1.)[0]-gap.info.objective(b-step, x, m, y, 1.)[0])/2e-6)
    np.testing.assert_allclose(grad, numerical, atol=1e-8)
    np.testing.assert_allclose(np.exp(np.log(m)), m)


def test_fit_normalizes_and_scaler_is_training_only():
    train = [dict(row(f'2020-01-0{i}', f'{lg}{i}', lg), y=i % 3) for lg in gap.base.LEAGUES for i in range(1, 7)]
    evaluation = [row('2021-01-01', lg, lg) for lg in gap.base.LEAGUES]
    features = {r['match_id']: np.ones(4)*i for i, r in enumerate(train)}
    features.update({r['match_id']: np.ones(4)*100 for r in evaluation})
    p, audits = gap.fit_predict(train, evaluation, features)
    np.testing.assert_allclose(p.sum(1), 1)
    assert np.isfinite(p).all() and (p > 0).all()
    assert audits[0]['scaler']['mean'] == [2.5]*4


def test_cash_budget_and_distinct_games():
    rows = [row('2020-01-01', 'a'), row('2020-01-01', 'b', home='C', away='D'), row('2020-01-02', 'cash')]
    rows[-1]['prices'] = None
    tickets = gap.base.choose_tickets(rows[:2], np.array([[.7, .2, .1]]*2), 'highestprob')
    ledger = gap.base.settle(rows, tickets)
    assert len(ledger) == 2
    assert ledger[-1]['stake'] == 0
    assert len({leg['match_id'] for leg in ledger[0]['legs']}) == 2


def test_guarded_fit_dates():
    train = [row('2019-12-25'), row('2019-12-26')]
    evaluation = [dict(row('2020-01-01'), season=20)]
    assert [r['date'] for r in gap.base.guarded_training(train, 19, evaluation)] == ['2019-12-25']


def test_protocol_guard_mocked_and_before_sources(monkeypatch):
    with patch.object(gap.subprocess, 'check_output', side_effect=[gap.PROTOCOL.read_bytes(), 'abc\n']):
        assert gap.verify_protocol() == 'abc'
    with patch.object(gap.subprocess, 'check_output', return_value=b'changed'):
        with pytest.raises(ValueError, match='protocol'):
            gap.verify_protocol()
    monkeypatch.setattr(gap.sys, 'argv', ['gap'])
    with patch.object(gap, 'verify_protocol', side_effect=ValueError('guard')), patch.object(gap.info, 'load_sources') as load:
        with pytest.raises(ValueError, match='guard'):
            gap.main()
        load.assert_not_called()


def test_artifact_hashes_and_selection():
    path = gap.ROOT/'docs/research/2026-10-01-gap-information-results.json'
    if not path.exists():
        pytest.skip('Empirical artifact not generated yet')
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['protocol_sha256_lf'] == gap.digest(gap.PROTOCOL)
    assert result['reference_sha256_lf'] == gap.digest(gap.REFERENCE)
    for name, expected in result['code_sha256_lf'].items():
        assert '\\' not in name
        assert gap.digest(gap.ROOT/name) == expected
    assert result['trial_count'] == 12 and result['actual_league_fits'] == 28
    for fold in result['folds']:
        candidates = [t for t in result['trials'] if t['inner_year'] == fold['inner_year']]
        assert fold['selected_id'] == min(candidates, key=lambda t: (t['metrics']['logloss'], t['id']))['id']
