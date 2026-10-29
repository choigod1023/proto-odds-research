import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_league_retune_results import DEFAULT, compare, selection, verify_league


def test_selection_uses_same_stake_and_saved_probabilities():
    records = [dict(date='2024-01-01', hg=3, ag=2, prices=[1.5, 3.0],
                    baseline_probability=.7, candidate_probabilities={'market_null': .7, 'calibrated': .8, 'underdog': .2}),
               dict(date='2024-01-02', hg=1, ag=2, prices=[1.5, 3.0],
                    baseline_probability=.7, candidate_probabilities={'market_null': .7, 'calibrated': .4, 'underdog': .2})]
    chosen, metrics = selection(records)
    assert chosen['primary'] == 'calibrated'
    assert chosen['hit_leader'] == 'calibrated'
    assert chosen['profit_leader'] == 'calibrated'
    assert metrics['calibrated']['net_krw'] == 25000


def test_incomplete_candidate_coverage_is_rejected():
    with pytest.raises(ValueError, match='coverage'):
        selection([{'candidate_probabilities': {'market_null': .6}},
                   {'candidate_probabilities': {'market_null': .6, 'new': .8}}])


def test_mismatched_saved_metric_is_rejected():
    with pytest.raises(AssertionError):
        compare(dict(n=2), dict(n=3), 'tampered')


@pytest.mark.parametrize('league,count', [('mlb', 25), ('kbo', 30)])
def test_committed_ledgers_match_independent_settlement_and_selection(league, count):
    result = verify_league(DEFAULT/league, league)
    assert result['all_saved_candidate_metrics_match']
    assert result['candidate_count'] == count
