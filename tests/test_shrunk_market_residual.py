"""Bounded study E regression checks, including the actual frozen corpus."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1] / 'experiments/shrunk-market-residual-20261030'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = module('study_e_residual', HERE / 'residual.py')
v = module('study_e_independent', HERE / 'verify.py')


@pytest.fixture(scope='module')
def computed():
    return m.run(stability_enabled=False)


def test_fixed_protocol_trials_committed_before_results():
    p = json.loads(m.PROTOCOL.read_text(encoding='utf-8'))
    assert p['trial_count'] == len(p['candidates']) == 10
    assert len({r['id'] for r in p['candidates']}) == 10
    assert p['base_commit'] == '421544799673a38eb7cb32f70397476bc1f507c2'


def test_analytic_gradient():
    rng = np.random.default_rng(45)
    z = rng.normal(size=(17, 9))
    theta = rng.normal(size=9)
    y = rng.integers(0, 2, size=17)
    offset = rng.normal(size=17)
    penalty = np.linspace(10, 100, 9)
    _, actual = m.objective(theta, z, y, offset, penalty)
    for j in range(9):
        d = np.zeros(9)
        d[j] = 1e-5
        numerical = (m.objective(theta + d, z, y, offset, penalty)[0] -
                     m.objective(theta - d, z, y, offset, penalty)[0]) / 2e-5
        assert abs(actual[j] - numerical) < 1e-7


def test_genuine_partial_pooling_has_shared_and_league_slopes():
    x = np.array([[1, 2], [1, 3], [1, 4]])
    z = m.design(x, ['KBO', 'NPB', 'MLB'], 'partial', m.LEAGUES)
    assert z.shape == (3, 8)  # global slopes + each league's slope deviations
    assert np.array_equal(z[:, :2], x)
    assert np.array_equal(z[:, 2:4], np.array([[1, 2], [0, 0], [0, 0]]))
    assert np.array_equal(z[:, 4:6], np.array([[0, 0], [1, 3], [0, 0]]))


def test_higher_deviation_penalty_shrinks_to_global(computed):
    for features in ['core', 'audited']:
        weak, strong = [computed['models'][f'{features}-partial-{n}'] for n in [10, 100]]
        width = len(weak['prep']['names']) + 1
        assert np.linalg.norm(strong['theta'][width:]) < np.linalg.norm(weak['theta'][width:])
        assert any(abs(x) > 1e-12 for x in strong['theta'])


def test_global_chronology_and_no_proxy_training(computed):
    cases, _, _ = m.target_cases(m.load('cases'), m.load('canonical_targets'))
    train, validation, test, split = m.chronological(cases)
    assert max(m.stamp(r['kickoff']) for r in train) < min(m.stamp(r['decision_at']) for r in validation) - m.timedelta(hours=48)
    assert max(r['day'] for r in validation) < min(r['day'] for r in test)
    assert all(r['cohort'] == 'retrospective_year_strict__claimed_time_only' for r in cases)
    assert all(r['external_p'] is not None for r in cases)
    assert sum(map(len, [train, validation, test])) + len(split['embargo_excluded']) == len(cases)


def test_same_odds_offset_and_actual_fixed_nine_to_one(computed):
    for r in computed['featured']:
        p = (1 / r['odds'][0]) / (1 / r['odds'][0] + 1 / r['odds'][1])
        assert p == r['offset_p']
        assert m.baseline([r], 'fixed9:1')[0] == .9 * r['market_p'][0] + .1 * r['external_p'][0]


def prediction_only(result):
    return [{k: r[k] for k in ['event_id', 'probabilities']} for r in result['predictions']]


def test_actual_test_labels_flip_every_parameter_and_prediction_unchanged(computed):
    raw = m.load('cases')
    ids = {r['event_id'] for r in computed['predictions']}
    flipped = copy.deepcopy(raw)
    for r in flipped:
        if r['event_id'] in ids and r['cohort'] == 'retrospective_year_strict__claimed_time_only':
            r['outcome'] = 1 - r['outcome']
    altered = m.run(raw=flipped, stability_enabled=False)
    assert altered['models'] == computed['models']  # includes preprocessing + coefficients
    assert altered['validation'] == computed['validation']
    assert altered['selected'] == computed['selected']
    assert prediction_only(altered) == prediction_only(computed)
    assert altered['overall'] != computed['overall']  # settlement must react


def test_validation_label_flip_cannot_train_or_predict(computed):
    raw = m.load('cases')
    start, end = computed['split']['validation_start'], computed['split']['test_start']
    for r in raw:
        if r['cohort'] == 'retrospective_year_strict__claimed_time_only' and r['league'] in m.LEAGUES and start <= r['day'] < end:
            r['outcome'] = 1 - r['outcome']
    changed = m.run(raw=raw, stability_enabled=False)
    assert changed['models'] == computed['models']
    assert prediction_only(changed) == prediction_only(computed)


def test_history_scores_after_freeze_cannot_affect_features_or_predictions(computed):
    history = m.load('history')
    for r in history:
        if r['date'] > computed['split']['train_freeze_day']:
            r['hg'], r['ag'] = 1000, 0
    changed = m.run(history=history, stability_enabled=False)
    assert changed['models'] == computed['models']
    assert changed['featured'] == computed['featured']
    assert prediction_only(changed) == prediction_only(computed)


def test_training_labels_positive_control_changes_fit(computed):
    raw = m.load('cases')
    for r in raw:
        if r['cohort'] == 'retrospective_year_strict__claimed_time_only' and r['league'] in m.LEAGUES and r['day'] <= computed['split']['train_freeze_day']:
            r['outcome'] = 1 - r['outcome']
    changed = m.run(raw=raw, stability_enabled=False)
    assert changed['models'] != computed['models']
    assert prediction_only(changed) != prediction_only(computed)


def test_postgame_and_final_odds_injected_fields_ignored(computed):
    raw = m.load('cases')
    for r in raw:
        r.update(final_odds=[1000, 1.01], current_label=1, target_lineup_after_game='poison',
                 postgame_win_probability=[0, 1], saved_final_p=[0, 1], saved_score_p=[1, 0])
    changed = m.run(raw=raw, stability_enabled=False)
    assert changed['models'] == computed['models']
    assert prediction_only(changed) == prediction_only(computed)


@pytest.fixture
def evidence():
    return dict(source_sha256=['a' * 64], observed_at='2026-10-30T00:00:00+00:00',
                max_fact_date='2026-05-01', selected_fact_dates=['2026-05-01'],
                fact_date_timezone='Asia/Seoul', source_game_ids=['prior'])


def test_retrospective_clock_does_not_authenticate(evidence):
    case = dict(event_id='target', decision_at='2026-05-04T00:00:00+00:00', day='2026-05-04')
    assert m.field_gate(3, evidence, case)[0]
    assert not m.field_gate(3, evidence, case, strict=True)[0]
    evidence.pop('observed_at')
    assert not m.field_gate(3, evidence, case)[0]


@pytest.mark.parametrize('poison', ['late_fact', 'target_id', 'hash', 'inconsistent_date', 'nan', 'naive_clock'])
def test_evidence_rejects_leaks(poison, evidence):
    case = dict(event_id='NPB:target', decision_at='2026-05-04T00:00:00+00:00', day='2026-05-04')
    value = 3
    if poison == 'late_fact':
        evidence['max_fact_time'] = '2026-05-04T01:00:00+00:00'
    elif poison == 'target_id':
        evidence['source_game_ids'] = ['target']
    elif poison == 'hash':
        evidence['source_sha256'] = ['wrong']
    elif poison == 'inconsistent_date':
        evidence['selected_fact_dates'] = ['2026-05-04']
    elif poison == 'nan':
        value = float('nan')
    elif poison == 'naive_clock':
        evidence['observed_at'] = '2026-05-03T01:00:00'
    assert not m.field_gate(value, evidence, case)[0]


def test_independent_entire_saved_corpus():
    checked = v.check(HERE / 'results')
    assert checked['event_settlements_exact'] == 378
    assert checked['metric_groups_exact'] == 56
    assert checked['paired_weekly_contrasts_exact'] == 90
    assert checked['holm_family_exact'] == 60


def test_all_input_hashes():
    for r in json.loads((HERE / 'provenance.json').read_text(encoding='utf-8')):
        assert m.sha(HERE / r['input_path']) == r['sha256']


def test_zero_residual_exact_market_and_no_home_constant_by_force(computed):
    model = copy.deepcopy(computed['models']['core-partial-100'])
    model['theta'] = [0.] * len(model['theta'])
    rows = computed['featured']
    assert np.allclose(m.predict(rows, model), [r['offset_p'] for r in rows], atol=1e-15, rtol=0)
    assert any(r['offset_p'] < .5 for r in rows)
    assert any(r['offset_p'] > .5 for r in rows)
