from copy import deepcopy
import math

import pytest

from scripts.evaluate_tipster_expansion import (
    capped_replay, certified, decimal_price, evaluate, market_comparison, settle,
)
from scripts.join_tipster_expansion import join


def pick(**changes):
    row = dict(source='one', author='author', source_url='https://example.test/article/1',
               fixture_id='mlb:1', league='MLB', match_date='2025-07-01',
               kickoff='2025-07-01T10:00:00+09:00', observed_at='2026-10-28T10:00:00+09:00',
               market='moneyline', side='home', period='full_game_including_overtime',
               home_score=4, away_score=2, result_evidence='official_exact_fixture',
               result_source_url='https://statsapi.mlb.com/example', reported_decimal_odds=2.,
               odds_time_quality='original_recommendation_claim', cutoff_proven=False)
    return dict(row, **changes)


def test_real_payout_push_and_missing_prices_have_separate_denominators():
    rows = [pick(), pick(fixture_id='2', side='away', reported_decimal_odds=3),
            pick(fixture_id='3', home_score=2, away_score=2),
            pick(fixture_id='4', reported_decimal_odds=None)]
    m = evaluate(rows)['settled']
    assert (m['picks'], m['wins'], m['losses'], m['pushes']) == (4, 2, 1, 1)
    assert m['hit_rate'] == pytest.approx(2/3)
    assert m['priced_picks'] == 3 and m['quoted_roi'] == 0


def test_current_prices_are_never_original_prices_and_unknown_period_rejected():
    r = evaluate([pick(odds_time_quality='current_card'), pick(fixture_id='2', period=None)])
    assert r['settled']['picks'] == 1 and r['settled']['quoted_roi'] is None
    assert r['excluded_counts']['unknown settlement period'] == 1


def test_duplicate_and_conflicting_quotes_do_not_select_favorable_result():
    first = pick()
    second = pick(source_url='https://example.test/article/2')
    assert evaluate([second, first])['settled']['picks'] == 1
    bad = pick(reported_decimal_odds=4)
    r = evaluate([first, bad])
    assert r['settled']['picks'] == 0
    assert r['excluded_counts']['conflicting_pick_or_quote'] == 2


def test_official_result_required_even_if_publisher_reports_win():
    r = evaluate([pick(result_evidence='publisher', source_reported_result='Won')])
    assert r['settled']['picks'] == 0


def test_spread_and_total_settlement_and_asian_exclusion():
    assert settle(pick(market='spread', line=-2.5)) == 'loss'
    assert settle(pick(market='spread', line=-2)) == 'push'
    assert settle(pick(market='total', line=5.5, side='over')) == 'win'
    with pytest.raises(ValueError, match='split Asian'):
        settle(pick(market='spread', line=-2.25))


def test_monthly_reservation_is_joint_across_leagues_and_does_not_overshoot():
    rows = [dict(pick(fixture_id=str(i), league='MLB' if i%2 else 'KBL'), settlement='loss') for i in range(20)]
    r = capped_replay(rows)
    assert r['bets'] == 5 and r['profit_krw'] == -50000
    assert r['monthly_net'] == {'2025-07': -50000}
    next_month = dict(pick(fixture_id='later', match_date='2025-08-01'), settlement='loss')
    assert capped_replay(rows+[next_month])['profit_krw'] == -60000


def test_prices_use_same_market_and_hybrid_can_change_pick():
    row = pick(proto_offer_evidence='archived_exact_fixture', proto_odds=[1.91,1.91], side='away')
    c = market_comparison(row)
    assert c['changed_from_market'] and c['picks']['market90_new_tipster10']=='away'
    assert c['unit_net']['tipster'] == -1
    assert c['unit_net']['archived_market_reference'] == pytest.approx(.91)
    assert market_comparison(pick()) is None


def test_late_scrape_is_not_certified_even_with_self_reported_early_post():
    row = pick(cutoff_proven=True, independent_pregame_evidence_url='https://archive.test/early')
    assert not certified(row)
    row['observed_at']='2025-07-01T09:29:00+09:00'
    assert certified(row)


@pytest.mark.parametrize('x', [True, None, math.nan, math.inf, -1, 1])
def test_invalid_decimal_prices(x):
    with pytest.raises((TypeError, ValueError)):
        decimal_price(x)


def test_american_prices():
    assert decimal_price(-140, True) == pytest.approx(1+100/140)
    assert decimal_price(170, True) == 2.7


def test_exact_join_deduplicates_official_local_and_kst_dates_but_not_doubleheaders():
    base=dict(league='MLB',home='Minnesota Twins',away='New York Yankees',home_score=7,away_score=0,
              official_id='mlb:123',source_url='https://statsapi.mlb.com/example')
    old=dict(base,date='2025-09-16',official_date='2025-09-15')
    fresh=dict(base,match_date='2025-09-15')
    c=dict(source='site',league='MLB',match_date='2025-09-15',teams=['Yankees','Twins'],
           market='moneyline',selected_team='Yankees')
    r=join([c],[old,fresh])[0]
    assert r['side']=='away' and r['home_score']==7
    other=dict(fresh,official_id='mlb:124')
    assert not join([c],[fresh,other])[0].get('fixture_id')


def test_result_conflict_cannot_be_cleared_by_third_copy():
    a=dict(league='KBO',home='LG',away='두산',home_score=2,away_score=1,
           match_date='2026-09-02',official_id='one',source_url='https://www.koreabaseball.com/example')
    c=dict(source='x',league='KBO',match_date='2026-09-02',teams=['LG 트윈스','두산 베어스'],
           market='moneyline',selected_team='LG')
    assert not join([c],[a,dict(a,home_score=0),a])[0].get('fixture_id')
