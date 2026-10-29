import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('objective', Path(__file__).resolve().parents[1]/'scripts/baseball_objective_diagnostics.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def row(**values):
    return dict(date='2024-04-01', hg=4, ag=2, prices=[1.4, 3.2], order=['home', 'away'],
                model_probability=.6, baseline_probability=.6, market_probability=.6, role='validation') | values


def test_most_likely_and_best_price_are_different_objectives():
    r = row()
    assert d.decide(r, .6, 'hit') == 'home'
    assert d.decide(r, .6, 'value') == 'away'
    assert d.decide(r, .6, 'edge10') == 'away'
    assert d.settle(r, 'home') == 4000
    assert d.settle(r, 'away') == -10000


def test_ev_threshold_and_abstention_do_not_get_fake_accuracy_or_roi():
    r = row(prices=[1.8, 1.8])
    assert d.decide(r, .5, 'edge0') is None
    result = d.measures([r], [.5], [None])
    assert result['bets'] == result['net_krw'] == 0
    assert result['accuracy'] is result['roi'] is None
    assert result['opportunity_roi'] == 0


def test_settlement_refund_order_and_no_odds_rounding():
    r = row(prices=[2.9, 1.49], order=['away', 'home'])
    assert d.settle(r, 'home') == 4900
    assert d.settle(dict(r, ag=4), 'away') == 0
    assert d.settle(r, None) == 0


def test_calibration_cannot_train_on_late_or_evaluation_results():
    for r in [row(date='2024-07-01'), row(role='evaluation')]:
        with pytest.raises(ValueError, match='early validation'):
            d.fit_calibrator([r])


def test_policy_selection_rejects_tiny_and_negative_return_samples():
    metrics = dict(tiny=dict(bets=24, net_krw=10000, roi=.5),
                   loss=dict(bets=50, net_krw=-100, roi=-.001))
    assert d.choose_validation(metrics, 100)['selected'] is None


def test_selected_roi_and_opportunity_return_use_different_denominators():
    records = [row(), row(date='2024-04-02')]
    result = d.measures(records, [.6, .6], ['home', None])
    assert result['bets'] == 1
    assert result['coverage'] == .5
    assert result['roi'] == .4
    assert result['opportunity_roi'] == .2


def test_oracle_flips_are_reported_as_hindsight_lower_bound():
    r = row(hg=1, ag=2)
    result = d.economic_audit([r])
    assert result['oracle_min_wrong_pick_flips_to_break_even'] == 1
    assert 'hindsight' in result['oracle_note']


def test_different_bet_counts_cannot_silently_truncate_outcomes():
    with pytest.raises(ValueError, match='misaligned'):
        d.measures([row()], [.6], [])


def test_fixed_9to1_can_be_structurally_unable_to_clear_both_prices():
    # Even a 100% vote gives p=.55, below 1/1.8 for either side.
    result = d.economic_audit([row(prices=[1.8, 1.8], market_probability=.5)])
    assert result['fixed_9to1_even_unanimous_cannot_clear_price_n'] == 1
    assert result['minimum_unanimous_vote_weight_any_side_quantiles'][1] == pytest.approx(1/9)


def test_future_results_cannot_change_frozen_calibration_or_policy(tmp_path):
    original = json.loads((d.DEFAULT/'frozen-policy.json').read_text(encoding='utf8'))
    for league in ('mlb', 'kbo'):
        records = d.read(d.DEFAULT/(league+'-inputs.jsonl'))
        for r in records:
            if r['role'] == 'evaluation':
                r.update(hg=99, ag=0)
        d.write(tmp_path/(league+'-inputs.jsonl'), records, lines=True)
    d.run(tmp_path)
    assert json.loads((tmp_path/'frozen-policy.json').read_text(encoding='utf8')) == original
