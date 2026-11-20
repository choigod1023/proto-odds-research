import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('information_audit', Path(__file__).resolve().parents[1]/'scripts/baseball_information_audit.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def test_changed_hit_count_can_leave_profit_unchanged_or_worse():
    records = [dict(date='2026-01-01', hg=2, ag=1, prices=[1.2, 5.0], order=['home', 'away']),
               dict(date='2026-01-02', hg=0, ag=1, prices=[1.5, 4.0], order=['home', 'away'])]
    result = a.score(records, [.6, .6], [.4, .4])
    assert result['wins'] == 1
    assert result['decomposition']['gained_payout_krw'] == 12000
    assert result['decomposition']['lost_payout_krw'] == 40000
    assert result['decomposition']['net_improvement_krw'] == -28000


def test_reference_audit_rejects_different_prices_on_same_game():
    row = dict(date='2026-01-01', hg=2, ag=1, prices=[1.2, 5.0], order=['home', 'away'])
    with pytest.raises(AssertionError, match='price changed'):
        a.match_reference(row, dict(row, prices=[1.3, 5.0]))


def test_integrated_models_match_reference_and_expose_constant_home_collapse():
    result = a.audit(a.DEFAULT)
    assert result['ok'] and result['reference_and_cohort_unchanged']
    assert result['evaluation']['mlb']['external230/full_fallback/all']['primary']['n'] == 230
    for group, n in [('old116/verified/all', 116), ('external230/verified/all', 194)]:
        variation = result['evaluation']['mlb'][group]['primary']['prediction_variation']
        assert variation['home_picks'] == n
        assert variation['away_picks'] == 0
        assert variation['identical_directions_to_all_home']
    assert result['evaluation']['kbo']['all']['baseline']['n'] == 198
