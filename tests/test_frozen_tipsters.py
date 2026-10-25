import copy
import pytest
from scripts.audit_frozen_tipsters import audit, decimal_odds


def sample():
    first = dict(pick_id='1', event_type='first_observed', identity_version=2,
        eligible_pre_event=True, game_started=False, result=None, american_odds=150,
        observed_at='2026-08-01T00:00:00Z', selection='HOME ML', slate_date='2026-08-01',
        team_a='HOME', team_b='AWAY', market_type='moneyline', slug='author')
    last = dict(first, event_type='changed', result='W', american_odds=200,
                observed_at='2026-08-02T00:00:00Z')
    return [first, last]


def test_frozen_odds_and_chronology():
    result = audit(reversed(sample()))
    assert result['overall']['roi_pct'] == 150
    assert result['incremental_roi_pct'] is None
    assert result['monthly_two_leg_simulation'] == 'not_run'


@pytest.mark.parametrize('field,value', [('result','W'), ('game_started',True),
    ('is_baseline',True), ('is_parlay_leg',True), ('event_type','changed'),
    ('eligible_pre_event',False), ('identity_version',1)])
def test_reject_unproven_first(field, value):
    rows = sample()
    rows[0][field] = value
    assert audit(rows)['overall']['n'] == 0


@pytest.mark.parametrize('value', [None, 0, 99, float('nan'), float('inf'), True])
def test_invalid_odds(value):
    with pytest.raises((ValueError, TypeError)):
        decimal_odds(value)


def test_no_backfill_missing_odds():
    rows = sample()
    rows[0]['american_odds'] = None
    assert audit(rows)['overall']['n'] == 0


def test_changed_selection():
    rows = sample()
    rows[1]['selection'] = 'AWAY ML'
    assert audit(rows)['excluded']['identity_changed'] == 1


def test_conflicting_result():
    rows = sample()
    rows.append(dict(rows[1], result='L'))
    assert audit(rows)['overall']['n'] == 0


def test_void_and_loss():
    for outcome, roi in [('V',0), ('P',0), ('L',-100)]:
        rows = sample()
        rows[1]['result'] = outcome
        assert audit(rows)['overall']['roi_pct'] == roi


def test_duplicate_excludes_both():
    rows = sample()
    second = copy.deepcopy(rows)
    for row in second:
        row['pick_id'] = '2'
    result = audit(rows+second)
    assert result['overall']['n'] == 0
    assert result['excluded']['duplicate_identity'] == 2
