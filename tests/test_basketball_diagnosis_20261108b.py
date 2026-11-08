import importlib.util
from pathlib import Path
import sys

import pytest
from scipy.stats import norm

path = Path(__file__).resolve().parents[1] / 'experiments/basketball-diagnosis-20261108b/audit.py'
spec = importlib.util.spec_from_file_location('basketball_audit', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_separate_markets_deduplicate_and_t30():
    item = {'market': '핸디캡', 'model': 'market-baseline-v1', 'offer_id': 'o'}
    pred = {'kickoff': '2026-11-07T10:00:00Z', 'captured_at': '2026-11-07T09:30:00Z',
            'prediction_snapshot_id': 'p', 'market': '승패', 'result': 'hit',
            'market_recommendations': {'items': [item]},
            'market_recommendation_results': {'o': {'result': 'miss'}}}
    row = {'sport': 'bk', 'league': 'KBL', 'prediction_record': pred}
    counts = module.audit({'generated_at': '2026-11-08T00:00:00Z', 'result_games': [row, row]})
    assert counts['7d/KBL/projection/승패/late_or_t30'] == {'hit': 1}
    assert counts['7d/KBL/market/핸디캡/market-baseline-v1/late_or_t30'] == {'miss': 1}
    assert counts['excluded']['missing_or_duplicate_snapshot_id'] == 1


def test_frozen_kbl_probability_matches_normal_and_market_price():
    sys.path.insert(0, str(path.parents[2] / 'src'))
    from datetime import datetime, timezone
    import league_handicap as model
    from market_pick_frontier import market_estimate

    fitted = model._models(model.ARTIFACT, model.ARTIFACT_SHA256)['KBL']
    aliases = list(fitted['aliases'])
    home = aliases[0]
    away = next(a for a in aliases if fitted['aliases'][a] != fitted['aliases'][home])
    offers = {s: {'배당': p, 'selection_id': s, 'offer_id': s} for s, p in
              [('핸디홈', 1.68), ('핸디원정', 1.80)]}
    terms = {'sport': 'bk', 'league': 'KBL', 'period': 'full_including_extra',
             'settlement_rule': 'handicap_half', 'market': 'handicap', 'line': -2.5}
    result = model.recommend(terms, {'home': home, 'away': away}, offers,
                             price_time=datetime(2026, 11, 6, tzinfo=timezone.utc))
    margin = fitted['mean_margin'] + (fitted['team_residual'][fitted['aliases'][home]] -
                                     fitted['team_residual'][fitted['aliases'][away]]) / 2
    p_home = norm.sf((2.5 - margin) / fitted['sd_margin'])
    assert result['probability'] == pytest.approx(max(p_home, 1 - p_home))
    market = market_estimate('핸디홈', offers, model='market', basis='market')
    assert market['probability'] == pytest.approx((1 / 1.68) / (1 / 1.68 + 1 / 1.80))


def test_ledger_uses_latest_revision_and_refuses_changed_terms():
    audit_path = path.with_name('ledger_audit.py')
    spec = importlib.util.spec_from_file_location('basketball_ledger', audit_path)
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    item = {'market': '핸디캡', 'model': 'market-baseline-v1', 'offer_id': 'o',
            'selection_id': 's', 'settlement_rule': 'handicap_half', 'line': 2.5, 'odds': 1.7}
    prediction = {'record_type': 'prediction', 'snapshot_id': 'p', 'event_id': 'e',
                  'kickoff': '2026-11-07T10:00:00Z', 'captured_at': '2026-11-07T09:00:00Z',
                  'features': {'fixture': {'sport': 'bk', 'league': 'KBL'},
                               'tipster_hybrid': {'authors': 0, 'selection_source': 'market_fallback'}},
                  'model': {'selection_policy': 'tipster-vote-v1'},
                  'predictions': {'market': '승패', 'selection_id': 'x', 'odds': 1.4,
                                  'market_recommendations': {'items': [item]}}}
    outcome = dict(item, result='hit', line=3.5)
    settlement = {'record_type': 'settlement', 'snapshot_id': 'p', 'ledger_sequence': 2,
                  'outcome': {'market_recommendation_results': {'o': outcome}}}
    old = {'record_type': 'settlement', 'snapshot_id': 'p', 'ledger_sequence': 1,
           'outcome': {'market_recommendation_results': {'o': dict(item, result='miss')}}}
    result = ledger.summarize([prediction, settlement, old])
    key = '7d/KBL/market_recommendation/핸디캡/market-baseline-v1/before_t30'
    assert result['groups'][key] == {'settlement_terms_mismatch': 1}
    key = '7d/KBL/primary_saved_selection/승패/tipster-vote-v1:market_fallback:authors_0/before_t30'
    assert result['groups'][key] == {'no_settlement_result': 1}
