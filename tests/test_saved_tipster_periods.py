import copy
import hashlib
import json
from datetime import datetime, timedelta

import pytest

from scripts.compare_saved_tipster_periods import load_observed_quotes, parse_native, reports
from research_vote_rankings import select_replayed_rules
from ai_decision import event_id


def envelope(**changes):
    row = dict(event_type='first_observed', source='tailslips_public_html', eligible_pre_event=True,
               game_started=False, result=None, is_baseline=False, is_parlay_leg=False,
               market_type='moneyline', slug='alice', pick_id='pick-1', selection='NYY ML',
               raw_text='NYY ML', team_a='NYY', team_b='BOS', game_header='NYY vs BOS 7:00 PM ET',
               slate_date='2026-07-01', observed_at='2026-07-01T22:00:00Z', american_odds=None)
    row.update(changes)
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'event': row, 'event_hash': hashlib.sha256(raw).hexdigest()}


def official_fixture():
    game = dict(year=2026, round=45, sport='bs', league='MLB', home='뉴욕양키스', away='보스턴', date='07.02(목) 08:00')
    eid = event_id(game)
    label = dict(event_id=eid, kickoff='2026-07-01T23:00:00Z', market='승패', label='', n_way=2,
                 result_text='3:1', result_action='결과보기', official_source={'game_no': '012'})
    row = dict(id=1, target_event_id=eid, season=2026, round=45, game_no='012', sport='bs', league='MLB',
               home=game['home'], away=game['away'], date_text=game['date'], market_family='승패',
               market_label='', n_way=2, observed_at='2026-07-01T22:00:00Z', odds_json='[1.8, 2.1]')
    return game, label, row


def load(tmp_path, rows, game, label):
    path = tmp_path / 'quotes.json'
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    return load_observed_quotes(path, {event_id(game): [(game, label)]})


def replay_feature():
    return dict(comparison_mode='counterfactual_saved_pregame_votes', capture_kind='quote_observation_not_model_prediction',
                snapshot_id='s1', event_id='e1', sport='bs', kickoff='2026-07-01T23:00:00Z',
                as_of='2026-07-01T22:00:00Z', captured_at=None, market_observed_at='2026-07-01T22:00:00Z',
                quote_observed_at='2026-07-01T22:00:00Z', market='승패', label='',
                vote_counts={'홈': 1, '원정': 1}, evidence=[
                    dict(id='1', author='a', side='홈', observed_at='2026-07-01T21:00:00Z'),
                    dict(id='2', author='b', side='원정', observed_at='2026-07-01T21:01:00Z')],
                options=[dict(selection='홈', market='승패', label='', line=None, odds=1.8, market_probability=.55),
                         dict(selection='원정', market='승패', label='', line=None, odds=2.1, market_probability=.45)])


def test_parse_native_hash_and_explicit_ml_without_quote():
    env = envelope()
    result = parse_native(env)
    assert result['selected_code'] == 'NYY' and result['has_expert_quote'] is False
    env['event']['selection'] = 'BOS ML'
    with pytest.raises(ValueError, match='hash'):
        parse_native(env)


@pytest.mark.parametrize('patch', [dict(event_type='graded'), dict(result='won'), dict(game_started=True),
    dict(eligible_pre_event=False), dict(is_baseline=True), dict(is_parlay_leg=True),
    dict(observed_at='2026-07-01T22:30:00Z'), dict(observed_at='2026-06-27T22:00:00Z')])
def test_native_pending_first_observation_single_pick_and_t30(patch):
    with pytest.raises(ValueError):
        parse_native(envelope(**patch))


def test_native_quoted_price_and_text_and_team_and_schedule_checks():
    assert parse_native(envelope(selection='NYY ML -110', raw_text='NYY ML -110', american_odds=-110))['has_expert_quote']
    for patch in (dict(selection='NYY ML -110', raw_text='NYY ML -110', american_odds=110),
                  dict(selection='NYY ML', raw_text='BOS ML'), dict(selection='LAD ML', raw_text='LAD ML'),
                  dict(game_header='NYY vs BOS'), dict(slate_date=None)):
        with pytest.raises(ValueError):
            parse_native(envelope(**patch))


@pytest.mark.xfail(strict=True, reason='native header teams are not checked against team_a/team_b')
def test_native_rejects_header_with_conflicting_teams():
    with pytest.raises(ValueError):
        parse_native(envelope(game_header='LAD vs SFG 7:00 PM ET'))


@pytest.mark.parametrize('patch', [dict(target_event_id='wrong'), dict(season=2025), dict(round=46),
    dict(game_no='013'), dict(home='다른팀'), dict(away='다른팀'), dict(date_text='07.03(금) 08:00'),
    dict(n_way=3), dict(market_family='승무패')])
def test_observed_quote_requires_exact_identity_and_product(tmp_path, patch):
    game, label, row = official_fixture()
    row.update(patch)
    quotes, held, _ = load(tmp_path, [row], game, label)
    assert not quotes and sum(held.values()) == 1


@pytest.mark.xfail(strict=True, reason='official n-way and market are not checked against observed product')
def test_observed_quote_rejects_official_three_way_for_two_way_row(tmp_path):
    game, label, row = official_fixture()
    label.update(n_way=3, market='승무패')
    quotes, held, _ = load(tmp_path, [row], game, label)
    assert not quotes and sum(held.values()) == 1


@pytest.mark.parametrize('observed', ['2026-07-01T22:30:00Z', '2026-06-30T22:59:59Z'])
def test_observed_quote_time_window(tmp_path, observed):
    game, label, row = official_fixture()
    row['observed_at'] = observed
    quotes, held, _ = load(tmp_path, [row], game, label)
    assert not quotes and held['quote_outside_fixed_24h_to_T30_window'] == 1


def test_observed_quote_metadata_and_conflicting_same_time_prices(tmp_path):
    game, label, row = official_fixture()
    quotes, held, _ = load(tmp_path, [row], game, label)
    assert len(quotes) == 1 and not held
    quote = quotes[0][0]
    assert quote['captured_at'] is None and quote['model'] == {} and quote['predictions'] == {}
    assert quote['capture_kind'] == 'quote_observation_not_model_prediction'
    assert quote['market_observed_at'] == row['observed_at']
    other = dict(row, id=2, odds_json='[1.9, 2.0]')
    quotes, held, _ = load(tmp_path, [row, other], game, label)
    assert not quotes and held['conflicting_simultaneous_quote_vectors'] == 1


def test_replayed_rules_abstain_on_vote_tie_and_guard_receipt_clock():
    feature = replay_feature()
    selected = select_replayed_rules(feature)
    assert selected['frozen_vote'] is None and selected['market_same_product'] == '홈'
    assert feature['captured_at'] is None
    for patch in (dict(captured_at='2026-07-01T22:00:00Z'), dict(quote_observed_at='2026-07-01T22:01:00Z'),
                  dict(as_of='2026-07-01T22:30:00Z', quote_observed_at='2026-07-01T22:30:00Z', market_observed_at='2026-07-01T22:30:00Z')):
        with pytest.raises(ValueError):
            select_replayed_rules(dict(feature, **patch))
    after = copy.deepcopy(feature)
    after['evidence'][0]['observed_at'] = '2026-07-01T22:00:01Z'
    with pytest.raises(ValueError):
        select_replayed_rules(after)


def test_reports_use_kst_calendar_month_at_utc_boundary():
    feature = replay_feature()
    feature.update(kickoff='2026-07-31T16:30:00Z', as_of='2026-07-31T15:00:00Z',
                   quote_observed_at='2026-07-31T15:00:00Z', market_observed_at='2026-07-31T15:00:00Z')
    for item in feature['evidence']:
        item['observed_at'] = '2026-07-31T14:00:00Z'
    label = dict(snapshot_id='s1', event_id='e1', kickoff=feature['kickoff'], market='승패', label='',
                 n_way=2, result_action='결과보기', result_text='3:1')
    result = reports([feature], [label])
    assert '2026-08' in result and '2026-07' not in result
    assert result['2026-08']['all_electorates']['eligible_fixtures'] == 1
    assert result['2026-08']['common_unique_vote_cohort'] is None
