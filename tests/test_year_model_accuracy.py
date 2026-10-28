"""Regression tests for the fixed retrospective comparison, not production picks."""
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.compare_year_model_accuracy import (
    MODELS, capped, compare, events_from_rows, interval, result, unit_net, valid_prices,
)


def row(**changes):
    base = dict(source='publisher', author='one', fixture_id='f1', league='KBO',
        match_date='2026-04-01', kickoff='2026-04-01T18:30:00+09:00',
        market='moneyline', period='full_game_including_overtime', side='home',
        home_score=3, away_score=2, result_evidence='official_exact_fixture',
        result_source_url='https://official.example/f1', proto_odds=[1.6, 2.4],
        proto_offer_evidence='archived_exact_fixture')
    return dict(base, **changes)


def test_duplicate_author_posts_are_one_vote_and_one_game():
    events, excluded, count = events_from_rows([row(), row(), row(author='two', side='away')])
    assert not excluded and count == len(events) == 1
    assert events[0]['voters'] == 2 and events[0]['vote_counts'] == {'home': 1, 'away': 1}
    assert events[0]['picks']['tipster_vote'] is None


def test_conflicting_author_is_removed_without_removing_other_voters():
    events, _, _ = events_from_rows([row(), row(side='away'), row(author='two', side='away')])
    assert events[0]['voters'] == 1 and events[0]['picks']['tipster_vote'] == 'away'
    assert events_from_rows([row(), row(side='away')])[1] == {'no unambiguous voters': 1}


def test_all_voters_can_share_one_fixture_price_but_must_not_mix_prices():
    events, _, _ = events_from_rows([row(), row(author='two', proto_odds=None, proto_offer_evidence=None)])
    assert events[0]['voters'] == 2
    assert events_from_rows([row(), row(author='two', proto_odds=[1.7, 2.2])])[1] == {
        'missing or conflicting prices': 1}


@pytest.mark.parametrize('changes,reason', [
    ({'match_date': '2025-10-28'}, 'outside_window'),
    ({'match_date': '2026-10-29'}, 'outside_window'),
    ({'period': 'regulation'}, 'unverified settlement scope'),
    ({'result_source_url': ''}, 'missing official result'),
    ({'home_score': True}, 'invalid fixture/score'),
    ({'proto_offer_evidence': None}, 'missing or conflicting prices'),
    ({'proto_odds': [float('nan'), 2.4]}, 'invalid full price vector'),
])
def test_invalid_or_unverifiable_inputs_are_excluded(changes, reason):
    events, excluded, _ = events_from_rows([row(**changes)])
    assert not events and excluded == {reason: 1}


def test_disagreeing_official_results_are_not_counted_twice():
    events, excluded, _ = events_from_rows([row(), row(author='two', away_score=4)])
    assert not events and excluded == {'inconsistent official fixture': 1}


def test_football_draw_is_loss_for_home_but_binary_draw_refunds():
    football, _, _ = events_from_rows([row(league='K리그1', period=None,
        proto_odds=[1.5, 3.0, 5.0], home_score=2, away_score=2)])
    baseball, _, _ = events_from_rows([row(home_score=2, away_score=2)])
    assert result(football[0], 'market') == 'loss' and unit_net(football[0], 'market') == -1
    assert result(baseball[0], 'market') == 'push' and unit_net(baseball[0], 'market') == 0
    stats = compare(baseball, 100)['common']['market']
    assert stats['pushes'] == 1 and stats['hit_rate'] is None and stats['unit_roi'] == 0


def test_tied_vote_abstentions_are_visible_in_coverage_not_counted_as_losses():
    events, _, _ = events_from_rows([row(), row(author='two', side='away'),
        row(fixture_id='f2', match_date='2026-04-08')])
    stats = compare(events, 100)
    assert stats['priced_pool'] == 2 and stats['common_selected'] == 1
    assert stats['model_specific']['market']['selected'] == 2
    vote = stats['model_specific']['tipster_vote']
    assert vote['selected'] == 1 and vote['abstained'] == 1 and vote['coverage'] == .5
    assert vote['wins'] == 1 and vote['losses'] == 0


def test_weight_direction_and_paired_changed_picks_are_correct():
    events, _, _ = events_from_rows([row(side='away')])
    picks = events[0]['picks']
    assert picks == {'market': 'home', 'market90_vote10': 'home',
                     'market75_vote25': 'away', 'market50_vote50': 'away', 'tipster_vote': 'away'}
    delta = compare(events, 100)['differences_vs_market']['tipster_vote']
    assert delta['changed_picks'] == delta['market_only_correct'] == 1
    assert delta['model_only_correct'] == 0 and delta['hit_rate_difference'] == -1


def test_monthly_limit_reserves_all_sports_and_resets_only_next_month():
    rows = [row(fixture_id=str(i), home_score=0, away_score=1,
        league='KBO' if i % 2 else 'MLB', kickoff=f'2026-04-01T{10+i:02d}:00:00+09:00')
        for i in range(7)]
    rows += [row(fixture_id='next-day', match_date='2026-04-02', home_score=0, away_score=1),
             row(fixture_id='new-month', match_date='2026-05-01', home_score=0, away_score=1)]
    events, _, _ = events_from_rows(rows)
    journal = capped(events, 'market')
    assert journal['bets'] == 6 and journal['monthly_net'] == {'2026-04': -50000, '2026-05': -10000}
    assert {r['fixture_id'] for r in journal['ledger']} == {'0', '1', '2', '3', '4', 'new-month'}


def test_same_day_winners_cannot_fund_additional_bets_or_change_initial_selection():
    events, _, _ = events_from_rows([row(fixture_id=str(i),
        kickoff=f'2026-04-01T{10+i:02d}:00:00+09:00') for i in range(7)])
    first = capped(events, 'market')
    altered = deepcopy(events)
    for e in altered:
        e['winner'] = 'away'
    second = capped(altered, 'market')
    assert first['bets'] == second['bets'] == 5
    assert [e['fixture_id'] for e in first['ledger']] == [e['fixture_id'] for e in second['ledger']]


def test_bootstrap_resamples_weeks_instead_of_treating_rows_as_independent():
    one = interval(['2026-04-01'] * 100, [1]*100, [1]*100, 100)
    assert one['observed_weeks'] == 1 and one['low'] is None
    two = interval(['2026-04-01', '2026-04-08'], [0, 1], [1, 1], 100)
    assert two['observed_weeks'] == 2 and two['low'] == 0 and two['high'] == 1
    assert interval(['2026-04-01', '2026-04-08'], [0, 0], [0, 0])['low'] is None
    with pytest.raises(ValueError, match='same length'):
        interval(['2026-04-01'], [], [1])


def test_bool_prices_are_not_valid_numbers():
    with pytest.raises(ValueError):
        valid_prices([True, 2.5], 2)


def test_saved_corpora_resettle_against_original_scores_and_respect_monthly_cap():
    from scripts.verify_year_model_accuracy import verify
    folder = Path(__file__).resolve().parents[1] / 'experiments/year-model-hit-20261029'
    checks = verify(folder)
    assert checks['strict-results.json']['common_fixtures'] == 492
    assert checks['standard-ot-results.json']['common_fixtures'] == 580
    assert all(c['monthly_loss_cap_reconciled'] for c in checks.values())
