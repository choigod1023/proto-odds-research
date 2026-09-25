from copy import deepcopy
from datetime import timedelta, datetime, timezone
import pytest
from test_recommendation_history import NOW, candidate
from shadow_combo import update_experiment, STRATEGIES


def feed(now, rows=None):
    return {"generated_at": now.isoformat(), "source_generated_at": now.isoformat(),
            "live_odds_at": now.isoformat(), "candidates": rows if rows is not None else
            [candidate(i, predicted_hit_prob=.65, probability_source="shin_market_fallback") for i in (1, 2)]}


def registered():
    yesterday = NOW - timedelta(days=1)
    return update_experiment(feed(yesterday, []), None, {}, yesterday)


def draft(rows=None):
    now = NOW + timedelta(hours=2, minutes=20)
    return update_experiment(feed(now, rows), registered(), {}, now)


def day(state):
    return state['days']['2026-09-06']


def test_preregister_tomorrow_28_days_and_no_historical_backfill():
    state = registered()
    assert state['start_at'] == '2026-09-06T00:00:00+09:00'
    assert datetime.fromisoformat(state['end_at']) - datetime.fromisoformat(state['start_at']) == timedelta(days=28)
    later = NOW + timedelta(hours=4)
    assert update_experiment(feed(later), state, {}, later)['days'] == {}
    assert update_experiment(feed(NOW), None, {}, NOW)['days'] == {}


def test_freeze_immutable_original_offers_and_pending():
    state = draft()
    original = deepcopy(state)
    cutoff = NOW + timedelta(hours=2, minutes=30)
    frozen = update_experiment(feed(cutoff, [candidate(3, predicted_hit_prob=.9)]), state, {}, cutoff)
    batch = day(frozen)['batches']['daily']
    assert batch['status'] == 'frozen'
    assert batch['rows'] == day(original)['batches']['daily']['rows']
    assert batch['tickets']['daily_two'][0]['independence_assumed_ev'] == pytest.approx(.65**2 * 1.6**2 - 1)
    assert frozen['summary']['strategies']['daily_two']['tickets_pending'] == 1
    assert frozen['summary']['strategies']['daily_two']['roi_on_settled_stake'] is None
    assert state == original


def test_stale_at_cutoff_not_replaced_by_late_data():
    state = update_experiment(feed(NOW), registered(), {}, NOW)
    later = NOW + timedelta(hours=2, minutes=30)
    out = update_experiment(feed(later), state, {}, later)
    assert day(out)['batches']['daily']['status'] == 'skipped_stale'
    assert out['summary']['coverage']['skipped_stale_batches'] == 2


@pytest.mark.parametrize('field', ['generated_at', 'source_generated_at', 'live_odds_at'])
@pytest.mark.parametrize('offset', [None, 1, -960])
def test_missing_future_stale_source_rejected(field, offset):
    p = feed(NOW)
    p[field] = (NOW+timedelta(seconds=offset)).isoformat() if offset is not None else None
    assert update_experiment(p, registered(), {}, NOW)['days'] == {}


def test_empty_partial_and_error_preserve_snapshot():
    state = draft()
    now = NOW + timedelta(hours=2, minutes=25)
    for p in (feed(now, []), {**feed(now), 'partial': True}, {**feed(now), 'error': 'unavailable'}):
        assert update_experiment(p, state, {}, now)['days'] == state['days']


def test_duplicate_event_and_insufficient_candidates():
    rows = [candidate(predicted_hit_prob=.65), candidate(game_no=2, predicted_hit_prob=.64)]
    state = update_experiment({}, draft(rows), {}, NOW + timedelta(hours=4))
    assert len(day(state)['batches']['daily']['rows']) == 1
    assert day(state)['batches']['daily']['tickets']['daily_two'] == []
    assert next(iter(day(state)['batches']['daily']['rows'].values()))['probability_source'] == 'unknown'


def test_thresholds_and_budget_are_fixed_before_results():
    rows = [candidate(i, predicted_hit_prob=p) for i,p in enumerate([.65,.62,.59,.56],1)]
    state = update_experiment({}, draft(rows), {}, NOW + timedelta(hours=4))
    stats = state['summary']['strategies']
    # Existing highlights select top three here: .56 is observed but not selected.
    assert [stats[n]['selected_events'] for n in STRATEGIES] == [3,3,2,2,2]
    assert stats['daily_two']['stake_units'] == 1
    for name in ('recommended_singles','p58_singles','p60_singles','slot_two'):
        assert stats[name]['stake_units'] == pytest.approx(.25)
    assert state['summary']['coverage']['observed_events'] == 4
    assert state['summary']['coverage']['p60'] == 2


def test_later_slot_still_accepts_new_games_after_daily_freeze():
    cutoff = NOW + timedelta(hours=2, minutes=30)
    state = update_experiment({}, draft(), {}, cutoff)
    original = deepcopy(day(state)['batches']['daily'])
    later = NOW + timedelta(hours=8, minutes=20)
    rows = [candidate(i, predicted_hit_prob=.7, kickoff_at='2026-09-06T18:00:00+09:00') for i in (3,4)]
    state = update_experiment(feed(later, rows), state, {}, later)
    state = update_experiment({}, state, {}, later+timedelta(minutes=10))
    assert day(state)['batches']['daily'] == original
    assert state['summary']['strategies']['slot_two']['selected_events'] == 4
    assert state['summary']['strategies']['slot_two']['stake_units'] == .5
    assert state['summary']['strategies']['daily_two']['selected_events'] == 2


def test_official_settlement_void_loss_and_feed_expiry():
    later = NOW + timedelta(days=1)
    prices = {'markets': {'1': {str(i): {**candidate(i), 'label': '', 'result': r}
                               for i,r in ((1,'홈승'),(2,'무효'))}}}
    state = update_experiment({}, draft(), prices, later)
    stats = state['summary']['strategies']
    assert stats['daily_two']['profit_units'] == pytest.approx(.6)
    assert stats['slot_two']['profit_units'] == pytest.approx(.15)
    assert update_experiment({}, state, {}, later)['summary'] == state['summary']
    prices['markets']['1']['2']['result'] = '홈패'
    state = update_experiment({}, state, prices, later)
    assert state['summary']['strategies']['daily_two']['roi_on_settled_stake'] == -1
    assert state['summary']['strategies']['daily_two']['closed_day_drawdown_units_excluding_pending_days'] == 1


def test_period_closes_without_auto_restart_but_keeps_pending():
    state = draft()
    after = datetime.fromisoformat(state['end_at']) + timedelta(days=1)
    rows = [candidate(8, kickoff_at=(after+timedelta(hours=2)).isoformat(), predicted_hit_prob=.7)]
    closed = update_experiment(feed(after, rows), state, {}, after)
    assert closed['start_at'] == state['start_at']
    assert closed['status'] == 'observation_closed_settlement_pending'
    assert len(closed['days']) == 1
    assert closed['summary']['strategies']['daily_two']['tickets_pending'] == 1


def test_database_ignores_injected_state_preserves_empty_and_stale_writer(tmp_path):
    from runtime_db import RuntimeDatabase
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [candidate(i, predicted_hit_prob=.65, kickoff_at=(now+timedelta(days=1)).isoformat()) for i in (1,2)]
    db = RuntimeDatabase(tmp_path / 'shadow.sqlite')
    p = {**feed(now, rows), 'shadow_experiment': {'forged': True}}
    db.store_artifact('today_combo', p)
    saved = db.get_artifact('today_combo')['shadow_experiment']
    assert saved['paper_only'] is True and len(saved['days']) == 1
    db.store_artifact('today_combo', feed(now, []))
    after_empty = db.get_artifact('today_combo')['shadow_experiment']
    assert after_empty['days'] == saved['days']
    assert after_empty['summary'] == saved['summary']
    db.store_artifact('today_combo', feed(now-timedelta(hours=1), rows))
    assert db.get_artifact('today_combo')['shadow_experiment'] == after_empty


def test_version_guard():
    old = {'policy':'other', 'days':{}}
    assert update_experiment(feed(NOW), old, {}, NOW) == old


def test_four_slots_never_exceed_daily_budget_and_no_repeat():
    state = registered()
    for slot, hour in enumerate((1,7,13,19)):
        kickoff = datetime(2026,9,6,hour,tzinfo=timezone(timedelta(hours=9)))
        rows = [candidate(10*slot+i, predicted_hit_prob=.65, kickoff_at=kickoff.isoformat()) for i in (1,2,3)]
        now = kickoff-timedelta(minutes=40)
        state = update_experiment(feed(now, rows), state, {}, now)
        state = update_experiment({}, state, {}, kickoff-timedelta(minutes=30))
    stats = state['summary']['strategies']
    for name in STRATEGIES:
        assert stats[name]['stake_units'] == pytest.approx(1)
    assert stats['slot_two']['selected_events'] == 8
    assert stats['daily_two']['selected_events'] == 2
    assert stats['recommended_singles']['selected_events'] == 12
    assert stats['slot_two']['observed_but_not_selected'] == 4
    assert stats['slot_two']['selection_rate_of_observed'] == pytest.approx(8/12)


def test_pending_day_excluded_from_closed_day_drawdown():
    later = NOW + timedelta(days=1)
    prices = {'markets': {'1': {'1': {**candidate(1), 'label':'', 'result':'홈패'}}}}
    state = update_experiment({}, draft(), prices, later)
    stats = state['summary']['strategies']['recommended_singles']
    assert stats['profit_units'] == -.125
    assert stats['pending_stake_units'] == .125
    assert stats['closed_day_profit_units'] == 0
    assert stats['observed_closed_settled_days'] == 0
