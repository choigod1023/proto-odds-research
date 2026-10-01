"""Synthetic causal checks for the frozen cold-start experiment; no downloads."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest

from scripts import local_gap_cold_start as cold
from scripts import local_gap_information as previous


FAMILIES = ('zero_constant', 'prior_constant', 'zero_fast', 'prior_fast')


def match(day, identity=None, home='A', away='B', league='D1', season=19,
          stats=(10., 6., 4., 2.)):
    return dict(match_id=identity or day, date=day, league=league, season=season,
                home=home, away=away, stats=dict(zip(('HS', 'AS', 'HST', 'AST'), stats)),
                prices=[1.5, 4., 6.], y=0, hg=2, ag=0)


def test_prior_seeds_correct_home_away_positions_from_released_history():
    rows = [match('2020-01-01', home='X', away='Y'),
            match('2020-01-08', home='A', away='B'),
            match('2020-01-09', home='B', away='A')]
    f, d = cold.feature_rows(rows, .05, .8, 'prior_constant')
    np.testing.assert_array_equal(f[rows[0]['match_id']], np.zeros(4))
    # A and B each seed [10,6,6,10] and [4,2,2,4]. Swapping venue
    # still predicts 10/6, not the swapped 6/10 that a wrong mapping gives.
    np.testing.assert_allclose(f[rows[1]['match_id']], [10, 6, 4, 2])
    np.testing.assert_allclose(f[rows[2]['match_id']], [10, 6, 4, 2])
    assert d[rows[1]['match_id']]['counts'] == [[0, 0], [0, 0]]


@pytest.mark.parametrize('family', FAMILIES)
def test_seven_day_release_and_cold_counts(family):
    rows = [match('2020-01-01'), match('2020-01-07'), match('2020-01-08')]
    f, d = cold.feature_rows(rows, .05, .8, family)
    np.testing.assert_array_equal(f['2020-01-07'], np.zeros(4))
    assert d['2020-01-07']['counts'] == [[0, 0], [0, 0]]
    assert d['2020-01-08']['counts'] == [[1, 1], [1, 1]]
    alpha = .2 if family.endswith('_fast') else .05
    np.testing.assert_allclose(f['2020-01-08'], alpha*.8*np.array([10, 6, 4, 2]))
    assert d['2020-01-08']['cold']


@pytest.mark.parametrize('family', FAMILIES)
def test_current_future_mutation_and_input_order_invariance(family):
    rows = [match('2020-01-01'), match('2020-01-08'), match('2020-01-09')]
    expected, diagnostic = cold.feature_rows(rows, .1, .5, family)
    changed = deepcopy(rows)
    for r in changed[1:]:
        r['stats'] = {key: 999. for key in r['stats']}
        r.update(y=2, hg=0, ag=30)
    actual, actual_d = cold.feature_rows(changed[::-1], .1, .5, family)
    assert diagnostic == actual_d
    for identity in expected:
        np.testing.assert_array_equal(actual[identity], expected[identity])


def test_prior_ignores_unreleased_same_day_and_other_league():
    rows = [match('2020-01-01', 'sp', league='SP1', stats=(100, 60, 40, 20)),
            match('2020-01-02', 'old', home='X', away='Y'),
            match('2020-01-08', 'early', home='E', away='F'),
            match('2020-01-09', 'a', home='C', away='D', stats=(900, 800, 700, 600)),
            match('2020-01-09', 'b', home='G', away='H')]
    f, _ = cold.feature_rows(rows, .05, .5, 'prior_constant')
    np.testing.assert_array_equal(f['early'], np.zeros(4))
    np.testing.assert_allclose(f['a'], [10, 6, 4, 2])
    np.testing.assert_allclose(f['b'], f['a'])


def test_missing_pairs_do_not_update_counts_or_prior_denominator():
    rows = [match('2020-01-01', stats=(None, 1000, 4, 2)),
            match('2020-01-08', 'existing'),
            match('2020-01-08', 'new', home='C', away='D')]
    f, d = cold.feature_rows(rows, .05, .8, 'prior_fast')
    assert d['existing']['counts'] == [[0, 1], [0, 1]]
    np.testing.assert_allclose(f['existing'], [0, 0, .64, .32])
    np.testing.assert_allclose(f['new'], [0, 0, 4, 2])
    assert d['new']['counts'] == [[0, 0], [0, 0]]


def test_prior_uses_cumulative_pair_mean_not_latest_or_future_mean():
    rows = [match('2020-01-01', 'one', home='X', away='Y', stats=(10, 6, 4, 2)),
            match('2020-01-02', 'two', home='X', away='Y', stats=(20, 10, 8, 4)),
            match('2020-01-03', 'unreleased', home='X', away='Y', stats=(900, 800, 70, 60)),
            match('2020-01-09', 'new', home='C', away='D')]
    f, _ = cold.feature_rows(rows, .05, .8, 'prior_constant')
    np.testing.assert_allclose(f['new'], [15, 8, 6, 3])


def test_valid_update_count_is_per_statistic():
    start = date(2020, 1, 1)
    rows = [match((start+timedelta(days=8*i)).isoformat(), str(i),
                  stats=(None, 6, 4, 2) if i == 0 else (10, 6, 4, 2)) for i in range(7)]
    _, d = cold.feature_rows(rows, .05, .8, 'zero_fast')
    assert d['6']['counts'] == [[5, 6], [5, 6]]
    assert d['6']['cold']


def test_sixth_update_fast_seventh_regular_with_different_team_rates():
    start = date(2020, 1, 1)
    rows = [match((start+timedelta(days=8*i)).isoformat(), f'warm{i}', away=f'O{i}') for i in range(6)]
    rows += [match((start+timedelta(days=48)).isoformat(), 'mixed'),
             match((start+timedelta(days=55)).isoformat(), 'after')]
    f, d = cold.feature_rows(rows, .05, .8, 'zero_fast')
    # Explicit oracle for A's first six games against previously unseen opponents.
    h = np.zeros((2, 4))
    for _ in range(6):
        for j, (sh, sa) in enumerate(((10, 6), (4, 2))):
            eh, ea = sh-h[j, 0]/2, sa-h[j, 1]/2
            h[j] += .2*np.array([.8*eh, .8*ea, .2*eh, .2*ea])
    np.testing.assert_allclose(f['mixed'], [h[0, 0]/2, h[0, 1]/2, h[1, 0]/2, h[1, 1]/2])
    assert d['warm5']['counts'] == [[5, 5], [0, 0]]
    assert d['mixed']['counts'] == [[6, 6], [0, 0]]
    expected = []
    for j, (sh, sa) in enumerate(((10, 6), (4, 2))):
        eh, ea = sh-h[j, 0]/2, sa-h[j, 1]/2
        # Home has 6 released updates -> .05, away has 0 -> .2.
        expected.extend([(h[j, 0]+.05*.8*eh+.2*.8*eh)/2,
                         (h[j, 1]+.05*.8*ea+.2*.8*ea)/2])
    np.testing.assert_allclose(f['after'], expected)
    assert d['after']['counts'] == [[7, 7], [1, 1]]


def test_cold_boundary_and_entrant_tag_persist_for_whole_season():
    start = date(2020, 1, 1)
    rows = [match((start+timedelta(days=8*i)).isoformat(), str(i)) for i in range(7)]
    rows += [match('2021-01-01', 'next', season=20), match('2021-01-09', 'next2', season=20),
             match('2023-01-01', 'return', season=22), match('2023-01-09', 'return2', season=22)]
    _, d = cold.feature_rows(rows, .05, .5, 'zero_fast')
    assert d['5']['cold'] and not d['6']['cold']
    assert d['0']['entrant'] and d['6']['entrant']
    assert not d['next']['entrant'] and not d['next2']['entrant']
    assert not d['next']['cold']  # Neither cold nor entrant: other group.
    assert d['return']['entrant'] and d['return2']['entrant']
    assert not d['return']['cold']  # Entrant does not imply cold.


@pytest.mark.parametrize('family', FAMILIES)
def test_returning_team_retains_state_and_counts(family):
    rows = [match('2020-01-01'), match('2020-01-08', 'probe', home='X', away='Y'),
            match('2023-01-01', 'return', season=22)]
    # Moving only the evaluation date/season must not reinitialize A or B.
    control = deepcopy(rows)
    control[-1].update(date='2020-01-16', season=19)
    f, d = cold.feature_rows(rows, .1, .8, family)
    original, original_d = cold.feature_rows(control, .1, .8, family)
    np.testing.assert_array_equal(f['return'], original['return'])
    assert d['return']['counts'] == original_d['return']['counts'] == [[1, 1], [1, 1]]


@pytest.mark.parametrize('alpha,phi', previous.GRID)
def test_zero_constant_matches_unchanged_gap(alpha, phi):
    start = date(2020, 1, 1)
    rows = [match((start+timedelta(days=3*i)).isoformat(), str(i),
                  home=f'T{i % 4}', away=f'T{(i+1) % 4}',
                  stats=(None if i % 7 == 0 else i+4., i+2., 2., 1.)) for i in range(20)]
    expected = previous.feature_rows(rows, alpha, phi)
    actual, _ = cold.feature_rows(rows, alpha, phi, 'zero_constant')
    for identity in expected:
        np.testing.assert_allclose(actual[identity], expected[identity], rtol=0, atol=1e-12)


def test_protocol_guard_uses_frozen_commit_and_normalized_bytes():
    with patch.object(cold.subprocess, 'check_output', side_effect=[cold.PROTOCOL.read_bytes(), 'e2f608dc\n']) as git:
        assert cold.verify_protocol() == 'e2f608dc'
        assert 'e2f608dc:' in git.call_args_list[0].args[0][-1]
    with patch.object(cold.subprocess, 'check_output', return_value=b'not frozen'):
        with pytest.raises(ValueError):
            cold.verify_protocol()


@pytest.fixture(scope='module')
def saved_result():
    path = cold.ROOT/'docs/research/2026-10-01-gap-cold-start-results.json'
    if not path.exists():
        pytest.skip('Cold-start empirical artifact not yet available')
    return json.loads(path.read_text(encoding='utf-8'))


def independent_metrics(ledger):
    days = len(ledger)
    tickets = sum(r['stake'] for r in ledger)
    wins = sum(r['won'] for r in ledger)
    profit = sum((Decimal(str(r['profit'])) for r in ledger), Decimal(0))
    return dict(days=days, tickets=tickets, wins=wins, profit=float(profit),
                coverage=tickets/days, budget_return=float(profit/Decimal(days)),
                roi=float(profit/Decimal(tickets)) if tickets else None,
                hit=wins/tickets if tickets else None)


def assert_metrics(actual, expected):
    for key, value in expected.items():
        if value is None:
            assert actual[key] is None
        else:
            assert actual[key] == pytest.approx(value, rel=0, abs=1e-10)


def audit_saved_record(record, matches, model):
    assert record['budget'] == 1
    legs = record['legs']
    assert len(legs) in (0, 2)
    assert len({leg['match_id'] for leg in legs}) == len(legs)
    payout = Decimal(1)
    won = bool(legs)
    for leg in legs:
        source = matches[leg['match_id']]
        assert (source['league'], source['date']) == (record['league'], record['date'])
        outcome = leg['outcome']
        assert outcome in (0, 1, 2)
        assert leg['odds'] == source['prices'][outcome]
        assert leg['probability'] == source['probabilities'][model][outcome]
        payout *= Decimal(str(source['prices'][outcome]))
        won = won and source['y'] == outcome
    stake = int(bool(legs))
    expected_profit = (payout if won else Decimal(0))-Decimal(stake)
    assert record['stake'] == stake
    assert record['won'] == int(won)
    assert abs(Decimal(str(record['profit']))-expected_profit) < Decimal('1e-10')


def test_saved_ledgers_independent_decimal_recomputation(saved_result):
    pooled = {}
    audited = 0
    for fold in saved_result['folds']:
        matches = {r['match_id']: r for r in fold['forecasts']}
        # Source has no missing odds; every actual match/date appears here.
        expected_days = {(r['league'], r['date']) for r in matches.values()}
        assert len(matches) == len(fold['statistic_forecasts']) == 686
        for model, policies in fold['ledgers'].items():
            for policy, ledger in policies.items():
                days = [(r['league'], r['date']) for r in ledger]
                assert len(days) == len(set(days)) and set(days) == expected_days
                for record in ledger:
                    audit_saved_record(record, matches, model)
                    audited += 1
                assert_metrics(fold['reports'][model]['policies'][policy]['metrics'], independent_metrics(ledger))
                pooled.setdefault((model, policy), []).extend(ledger)
    for (model, policy), ledger in pooled.items():
        assert_metrics(saved_result['pooled'][model]['policies'][policy]['metrics'], independent_metrics(ledger))
    assert audited == 482*11*5 == 26510


def test_independent_audit_rejects_wrong_payout_or_source():
    matches = {str(i): dict(league='D1', date='2020-01-01', prices=[1.9, 3, 4], y=0,
                           probabilities={'model': [.6, .2, .2]}) for i in range(2)}
    record = dict(league='D1', date='2020-01-01', budget=1, stake=1, won=1, profit=2.61,
                  legs=[dict(match_id=str(i), outcome=0, odds=1.9, probability=.6) for i in range(2)])
    audit_saved_record(record, matches, 'model')
    for key, bad in (('profit', 3), ('won', 0), ('stake', 0), ('budget', 2)):
        changed = deepcopy(record)
        changed[key] = bad
        with pytest.raises(AssertionError):
            audit_saved_record(changed, matches, 'model')
    changed = deepcopy(record)
    changed['legs'][1]['match_id'] = '0'
    with pytest.raises(AssertionError):
        audit_saved_record(changed, matches, 'model')


def test_saved_previous_gap_reproduction(saved_result):
    reference = json.loads(cold.REFERENCE.read_text(encoding='utf-8'))
    assert saved_result['source'] == reference['source']
    for fold in saved_result['folds']:
        old = next(f for f in reference['folds'] if f['outer_year'] == fold['outer_year'])
        old_rows = {r['match_id']: r for r in old['forecasts']}
        assert set(old_rows) == {r['match_id'] for r in fold['forecasts']}
        differences = []
        for r in fold['forecasts']:
            old_r = old_rows[r['match_id']]
            for key in ('league', 'date', 'y', 'prices'):
                assert r[key] == old_r[key]
            for model in ('old_gap', 'selected_zero_constant', 'fixed_zero_constant'):
                np.testing.assert_allclose(r['probabilities'][model], old_r['probabilities']['gap'], rtol=0, atol=1e-10)
            differences.extend(np.abs(np.array(r['probabilities']['selected_zero_constant'])-old_r['probabilities']['gap']))
        assert fold['reproduction_max_abs'] == pytest.approx(max(differences), abs=1e-15)
        assert fold['selected']['zero_constant'] == 'zero_constant:'+old['selected_id']
        assert fold['ledgers']['old_gap'] == old['ledgers']['gap']


def test_saved_hashes_counts_selection_and_cohorts(saved_result):
    def digest(path):
        return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    assert saved_result['protocol_sha256_lf'] == digest(cold.PROTOCOL)
    assert saved_result['reference_sha256_lf'] == digest(cold.REFERENCE)
    for name, expected in saved_result['code_sha256_lf'].items():
        assert '\\' not in name
        assert digest(cold.ROOT/name) == expected
    assert saved_result['trial_count'] == len(saved_result['trials']) == 48
    fits = sum(len(t['fit_audit']) for t in saved_result['trials'])
    fits += sum(len(a) for f in saved_result['folds'] for a in f['fit_audits'].values())
    assert saved_result['actual_league_fits'] == fits == 116
    assert saved_result['seed'] == 20261001 and saved_result['bootstrap_replicates'] == 5000
    assert saved_result['optimizer_failures'] == []
    for fold in saved_result['folds']:
        candidates = [t for t in saved_result['trials'] if t['inner_year'] == fold['outer_year']-1]
        assert len(candidates) == 24
        assert fold['winner'] == min(candidates, key=lambda t: (t['metrics']['logloss'], t['id']))['id']
        assert fold['modes']['inner_selected'] == fold['winner']
        for family in FAMILIES:
            group = [t for t in candidates if t['family'] == family]
            assert len(group) == 6
            assert fold['selected'][family] == min(group, key=lambda t: (t['metrics']['logloss'], t['id']))['id']
        for row in fold['statistic_forecasts']:
            cohort = row['cohorts']
            assert cohort['cold'] == any(count < 6 for team in cohort['counts'] for count in team)
        cohorts = [r['cohorts'] for r in fold['statistic_forecasts']]
        expected_counts = dict(all=len(cohorts), cold=sum(c['cold'] for c in cohorts),
                               entrant=sum(c['entrant'] for c in cohorts),
                               other=sum(not c['cold'] and not c['entrant'] for c in cohorts))
        for statistics in fold['statistics'].values():
            assert {k: v['matches'] for k, v in statistics.items()} == expected_counts
