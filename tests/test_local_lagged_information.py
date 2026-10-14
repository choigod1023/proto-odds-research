from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import local_lagged_information as info


def row(i, day, y=0, league='D1', home='A', away='B'):
    return dict(match_id=f'{league}:{i}', date=day, league=league, season=19,
                home=home, away=away, y=y, hg=(2, 1, 0)[y], ag=(0, 1, 2)[y],
                prices=[1.8, 3.2, 4.], stats={'HS': 12., 'AS': 6., 'HST': 5., 'AST': 2.})


def training():
    return [row(i, (date(2019, 1, 1)+timedelta(days=i*8)).isoformat(), i % 3,
                home=('A', 'B', 'C')[i % 3], away=('B', 'C', 'A')[i % 3]) for i in range(30)]


def test_market_offset_gradient_and_zero_coefficients():
    rng = np.random.default_rng(42)
    x = np.column_stack([np.ones(12), rng.normal(size=(12, 3))])
    p = rng.dirichlet([2, 2, 2], size=12)
    y = np.arange(12) % 3
    beta = rng.normal(size=12)/10
    objective = lambda b: info.objective(b, x, p, y, .1)
    assert check_grad(lambda b: objective(b)[0], lambda b: objective(b)[1], beta) < 1e-5
    value, _ = info.objective(np.zeros(12), x, p, y, .1)
    assert np.isclose(value, -np.log(p[np.arange(12), y]).mean())


def test_current_future_and_six_day_statistics_do_not_change_features():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-02'), row(2, '2020-01-08'), row(3, '2020-01-09')]
    original = info.feature_rows(rows)
    changed = deepcopy(rows)
    for r in changed[1:]:
        r.update(y=2, hg=0, ag=99, stats={key: 999. for key in info.STAT_KEYS})
    mutated = info.feature_rows(changed)
    for r in rows[:3]:
        assert np.array_equal(original[r['match_id']], mutated[r['match_id']], equal_nan=True)
    assert not np.array_equal(original[rows[-1]['match_id']], mutated[rows[-1]['match_id']], equal_nan=True)
    assert np.isnan(original[rows[1]['match_id']][0])
    assert original[rows[2]['match_id']][0] == 6  # Exactly seven days allowed.
    assert original[rows[2]['match_id']][4] == 6  # Recent schedule, no result use.


def test_same_day_schedule_batch_and_league_isolation():
    rows = [row(0, '2020-01-01'), row(1, '2020-01-01'), row(2, '2020-01-08', league='SP1')]
    features = info.feature_rows(rows)
    assert np.isnan(features[rows[1]['match_id']][4])
    assert np.isnan(features[rows[2]['match_id']][0])
    assert features[rows[2]['match_id']][6] == 0


def test_last_eight_window_and_surprise_away_sign():
    rows = [row(i, (date(2020, 1, 1)+timedelta(days=8*i)).isoformat()) for i in range(10)]
    rows[0]['stats']['HS'] = 1000.
    f = info.feature_rows(rows)[rows[-1]['match_id']]
    assert f[0] == 6 and f[1] == -6
    assert np.isclose(f[10], -f[11])


def test_scalers_impute_from_train_only_and_neutral_all_missing():
    train = np.array([[1., np.nan], [3., np.nan]])
    scaler = info.fit_scaler(train)
    assert np.array_equal(scaler['median'], [2, 0])
    before = {k: v.copy() for k, v in scaler.items()}
    transformed = info.transform(np.array([[999., 123.], [np.nan, np.nan]]), scaler)
    assert np.isfinite(transformed).all()
    assert all(np.array_equal(scaler[k], before[k]) for k in scaler)
    assert np.array_equal(transformed[1, -2:], [1, 1])


@pytest.mark.parametrize('family', list(info.FAMILIES))
def test_fitted_probabilities_and_current_stat_mutation(family):
    train = training()
    current = dict(row(31, '2021-01-01'), season=20)
    features = info.feature_rows(train+[current])
    model, _ = info.fit_model(train, features, family, 1.)
    p = info.predict(model, [current], features)
    info.base.validate(p)
    changed = dict(current, y=2, hg=99, ag=77, stats={key: 999. for key in info.STAT_KEYS})
    assert np.array_equal(p, info.predict(model, [changed], info.feature_rows(train+[changed])))


def test_fold_guard_and_missing_price_cash():
    sample = [row(0, '2020-07-01'), row(1, '2020-07-02'), dict(row(2, '2020-07-08'), season=20)]
    assert len(info.base.guarded_training(sample, 19, sample[-1:])) == 1
    sample[1]['date'] = sample[0]['date']
    sample[-1]['prices'] = None
    tickets = info.base.choose_tickets(sample[:2], np.array([[.7, .2, .1]]*2), 'highestprob')
    ledger = info.base.settle(sample, tickets)
    assert len(ledger) == 2 and ledger[-1]['stake'] == 0
    assert len({leg['match_id'] for leg in ledger[0]['legs']}) == 2


def test_protocol_guard_mocked_for_squash_ci(monkeypatch):
    raw = info.PROTOCOL.read_bytes()
    monkeypatch.setattr(info.subprocess, 'check_output', lambda cmd, **kwargs: 'fixed\n' if 'rev-parse' in cmd else info.lf(raw))
    assert info.verify_protocol() == 'fixed'
    monkeypatch.setattr(info.subprocess, 'check_output', lambda *a, **k: b'wrong')
    with pytest.raises(ValueError, match='Frozen'):
        info.verify_protocol()


def test_final_artifact_hashes_and_trial_counts():
    path = info.ROOT/'docs/research/2026-10-01-lagged-information-results.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['trial_count'] == len(result['trials']) == 50
    assert result['actual_league_fits'] == 128
    assert result['protocol_sha256_lf'] == hashlib.sha256(info.lf(info.PROTOCOL.read_bytes())).hexdigest()
    for relative, digest in result['code_sha256_lf'].items():
        assert '\\' not in relative
        assert hashlib.sha256(info.lf((info.ROOT/relative).read_bytes())).hexdigest() == digest
