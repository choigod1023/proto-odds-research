from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import poisson

from research.m1_service import model
from research.m1_service.low_odds import estimate, review
from research.m1_service.observer import Recorder, canonical, digest, moment, read

FIXTURES = Path(__file__).resolve().parents[1]/'research/m1_service/replay-fixtures.json'


def test_frozen_coefficients_reproduce_preserved_real_rows():
    cases = json.loads(FIXTURES.read_bytes())['cases']
    for case in cases:
        actual = model.forecast(case['row'])
        for name, prediction in actual.items():
            for side in ('home', 'draw', 'away'):
                assert prediction['probabilities_3way'][side] == pytest.approx(case['expected'][name]['probabilities_3way'][side], abs=1e-12)
            assert prediction['home_rate'] == pytest.approx(case['expected'][name]['home_rate'], abs=1e-11)
            assert not prediction['affects_selection']


def test_role_order_changes_only_identity_model_and_unknown_ids_are_explicit():
    row = deepcopy(json.loads(FIXTURES.read_bytes())['cases'][0]['row'])
    before = model.forecast(row)
    row['roster']['home']['hitters'][0], row['roster']['home']['hitters'][-1] = row['roster']['home']['hitters'][-1], row['roster']['home']['hitters'][0]
    after = model.forecast(row)
    assert after['m0_actual']['home_rate'] == before['m0_actual']['home_rate']
    assert after['m1_projected']['home_rate'] != before['m1_projected']['home_rate']
    row['roster']['home']['hitters'][0] = 'unknown-future-player'
    assert model.forecast(row)['m1_projected']['roster_coverage']['home'][1] >= 1/9
    row['features']['home_rest_days']['value'] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        model.forecast(row)


def test_null_numeric_is_not_observed_zero():
    row = deepcopy(json.loads(FIXTURES.read_bytes())['cases'][0]['row'])
    row['features']['home_rest_days']['value'] = None
    missing = model.forecast(row)['m1_projected']['home_rate']
    row['features']['home_rest_days']['value'] = 0
    assert model.forecast(row)['m1_projected']['home_rate'] != missing


def test_team_alias_empty_roster_and_unsupported_park_feature_are_rejected():
    row = deepcopy(json.loads(FIXTURES.read_bytes())['cases'][0]['row'])
    bad = deepcopy(row); bad.update(home_id='HT', away_id='KIA')
    with pytest.raises(ValueError, match='distinct teams'):
        model.forecast(bad)
    bad = deepcopy(row); bad['roster']['home']['hitters'] = []
    with pytest.raises(ValueError, match='ordered hitter'):
        model.forecast(bad)
    bad = deepcopy(row); bad['features']['park_factor']['value'] = 100
    with pytest.raises(ValueError, match='park_factor'):
        model.forecast(bad)
    bad = deepcopy(row); bad.update(season=2025, event_date='2025-07-19')
    with pytest.raises(ValueError, match='training-season'):
        model.forecast(bad)


def offer(kind='full_game_2way', side='home', odds=1.2, line=None):
    return {'fixture_id': 'case', 'offer_id': kind+':'+side+':'+str(line), 'period': 'full_game',
            'kind': kind, 'selection': side, 'odds': odds, 'line': line,
            'line_basis': 'home_score_plus_line',
            'settlement_rule': 'draw_void_refund' if kind == 'full_game_2way' else 'push_refund'}


def rate_forecast():
    return {'fixture_id': 'case', 'home_rate': 4., 'away_rate': 3.,
            'probabilities_3way': {'home': .8, 'draw': .1, 'away': .1}}


def test_refund_ev_uses_unconditional_probability():
    result = estimate(rate_forecast(), offer())
    assert result['p_push'] == .1
    assert result['estimated_net_roi'] == pytest.approx(.8*.2-.1)
    assert result['conditional_decisive_hit_probability'] == pytest.approx(.8/.9)
    offers = [offer(), offer(side='away', odds=5.)]
    r = review(rate_forecast(), offers, offers[0]['offer_id'])
    assert r['trigger_at_or_below_1_20'] and r['automatic_replacement'] is None
    offers[0]['odds'] = 1.20000001
    assert not review(rate_forecast(), offers, offers[0]['offer_id'])['trigger_at_or_below_1_20']


@pytest.mark.parametrize('kind,line,side', [('full_game_total', 7., 'under'), ('full_game_total', 7.5, 'over'),
                                         ('full_game_handicap', -1., 'home'), ('full_game_handicap', 1.5, 'away')])
def test_market_probabilities_against_independent_joint_score_sum(kind, line, side):
    result = estimate(rate_forecast(), offer(kind, side, line=line))
    grid = np.arange(60); joint = np.outer(poisson.pmf(grid, 4.), poisson.pmf(grid, 3.))
    value = grid[:, None]+grid[None, :] - line if kind == 'full_game_total' else grid[:, None]-grid[None, :]+line
    win = joint[value < 0].sum() if side in ('under', 'away') else joint[value > 0].sum()
    assert result['p_win'] == pytest.approx(win, abs=1e-12)
    assert result['p_push'] == pytest.approx(joint[value == 0].sum(), abs=1e-12)


def test_unsupported_lines_periods_and_missing_calibration_never_create_recommendation():
    bad = offer('full_game_handicap', line=.25)
    good = offer()
    result = review(rate_forecast(), [good, bad], good['offer_id'])
    assert len(result['excluded']) == 1 and result['automatic_replacement'] is None
    bad['period'] = 'first_half'
    with pytest.raises(ValueError, match='full-game'):
        estimate(rate_forecast(), bad)
    near_integer = offer('full_game_total', 'over', line=4.0000000000001)
    with pytest.raises(ValueError, match='whole/half'):
        estimate(rate_forecast(), near_integer)
    invalid = offer(odds=float('nan'))
    with pytest.raises(ValueError, match='finite numeric'):
        review(rate_forecast(), [invalid], invalid['offer_id'])
    fair = rate_forecast(); fair['probabilities_3way'] = {'home': 1/1.2, 'draw': 0., 'away': 1-1/1.2}
    original = offer()
    assert review(fair, [original], original['offer_id'])['positive_estimated_ev_frontier_offer_ids'] == []


def packet():
    """Synthetic timing/provenance fixture, never a reported real prediction."""
    row = deepcopy(json.loads(FIXTURES.read_bytes())['cases'][0]['row'])
    row.update(fixture_id='20261105HTSK02026', event_date='2026-11-05', home_id='SK', away_id='HT')
    row['history_coverage'] = {s: {'status': 'complete'} for s in ('home', 'away')}
    now = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)
    event = {'official_game_id': '20261105HTSK0', 'season': 2026, 'home_id': 'SK', 'away_id': 'HT', 'kickoff_at': '2026-11-05T09:30:00+00:00'}
    receipts = {}
    for k in ('announcement', 'history', 'odds'):
        body = {'test_only': True, 'kind': k}
        receipts[k] = {'uri': 'https://www.koreabaseball.com/test', 'received_at': now.isoformat(), 'body': body, 'sha256': digest(canonical(body))}
    market = {'market_id': 'synthetic-two-way', 'kind': 'full_game_2way', 'settlement_rule': 'draw_void_refund',
              'odds': {'home': '1.20', 'away': '1.20'}, 'close_at': event['kickoff_at'], 'round_close_at': event['kickoff_at']}
    return {'event': event, 'row': row, 'market': market, 'evidence': receipts}, now


def test_validator(payload, now, unpriced=False):
    # Dependency seam: upstream collector's real validators have their own
    # regression suite. These fixtures do not certify any source or game.
    if now > moment(payload['event']['kickoff_at'])-timedelta(minutes=30):
        raise ValueError('T-30')
    for ev in payload['evidence'].values():
        if moment(ev['received_at']) > now:
            raise ValueError('future receipt')

test_validator.__test__ = False


def final_result(p, now, h=0, a=0):
    event = p['event']; body = {'game': [{'G_ID': event['official_game_id'], 'HOME_ID': event['home_id'],
        'AWAY_ID': event['away_id'], 'G_DT': '20261105', 'SEASON_ID': '2026', 'B_SCORE_CN': str(h), 'T_SCORE_CN': str(a),
        'GAME_STATE_SC': '3', 'GAME_RESULT_CK': '1', 'CANCEL_SC_ID': '0'}]}
    return {**event, 'game_date': '2026-11-05', 'home_score': h, 'away_score': a, 'status': 'final',
            'evidence': {'uri': 'https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList',
                         'body': body, 'sha256': digest(canonical(body)), 'received_at': now.isoformat()}}


def test_record_restart_corrections_refunds_and_official_result_binding(tmp_path, monkeypatch):
    from research.m1_service import observer
    # Synthetic lifecycle tests isolate source reconstruction, which is tested
    # separately. No synthetic record enters the real research measurements.
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(observer, '_verify_market_bytes', lambda *a: None)
    p, now = packet(); recorder = Recorder(tmp_path/'m1.jsonl', test_validator, clock=lambda: now)
    first = recorder.capture(p); before = recorder.path.read_bytes()
    assert first['low_odds_review']['automatic_replacement'] is None
    assert recorder.capture(p) is None and recorder.path.read_bytes() == before
    end = moment(p['event']['kickoff_at'])+timedelta(hours=3); recorder.clock = lambda: end
    result = final_result(p, end)
    assert recorder.settle(result)
    assert recorder.summary()['methods']['m1_projected']['roi'] == 0
    assert recorder.settle(result) == []
    end += timedelta(minutes=1)
    result = final_result(p, end, 9, 0)
    assert recorder.settle(result)[0]['supersedes_hash']
    assert recorder.summary()['settled'] == 1 and recorder.path.read_bytes().startswith(before)
    older = final_result(p, end-timedelta(seconds=30))
    with pytest.raises(ValueError, match='moved backwards'):
        recorder.settle(older)
    wrong_team = final_result(p, end+timedelta(seconds=1), 9, 0)
    wrong_team['home_id'] = 'LG'; wrong_team['evidence']['body']['game'][0]['HOME_ID'] = 'LG'
    wrong_team['evidence']['sha256'] = digest(canonical(wrong_team['evidence']['body']))
    recorder.clock = lambda: end+timedelta(minutes=1)
    with pytest.raises(ValueError, match='frozen capture identity'):
        recorder.settle(wrong_team)
    result['home_score'] = 8
    with pytest.raises(ValueError, match='score differs'):
        recorder.settle(result)


def test_late_or_incomplete_packet_rejected_and_unpriced_never_has_roi(tmp_path, monkeypatch):
    from research.m1_service import observer
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(observer, '_verify_market_bytes', lambda *a: None)
    p, now = packet(); path = tmp_path/'m1.jsonl'
    ticks = iter([now, moment(p['event']['kickoff_at'])-timedelta(minutes=29)])
    recorder = Recorder(path, test_validator, clock=lambda: next(ticks))
    with pytest.raises(ValueError, match='completed after T-30'):
        recorder.capture(p)
    assert not path.exists()
    recorder.clock = lambda: now
    p['row']['history_coverage']['home']['status'] = 'incomplete'
    with pytest.raises(ValueError, match='history coverage'):
        recorder.capture(p)
    p['row']['history_coverage']['home']['status'] = 'complete'
    p.pop('market'); p['evidence'].pop('odds')
    assert recorder.capture(p, unpriced=True)['roi_eligible'] is False
    assert recorder.summary()['methods']['m1_projected']['roi'] is None
    recorder.path.write_bytes(recorder.path.read_bytes().replace(b'"promoted":false', b'"promoted":true'))
    with pytest.raises(ValueError, match='hash chain'):
        read(path)


def test_model_artifact_cannot_be_backdated_into_earlier_capture(tmp_path):
    p, _ = packet()
    before = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    recorder = Recorder(tmp_path/'m1.jsonl', test_validator, clock=lambda: before)
    with pytest.raises(ValueError, match='predates preserved coefficient'):
        recorder.capture(p)
    assert not recorder.path.exists()


def test_two_and_three_way_paper_results_stay_separate(tmp_path, monkeypatch):
    from research.m1_service import observer
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(observer, '_verify_market_bytes', lambda *a: None)
    p, now = packet(); r = Recorder(tmp_path/'m1.jsonl', test_validator, clock=lambda: now)
    r.capture(p)
    three = deepcopy(p)
    three['market'].update(market_id='synthetic-three-way', kind='full_game_3way', settlement_rule='draw_is_outcome')
    three['market']['odds']['draw'] = '8.0'
    r.capture(three)
    end = moment(p['event']['kickoff_at'])+timedelta(hours=3); r.clock = lambda: end
    r.settle(final_result(p, end))
    result = r.summary()
    assert result['captured'] == 2 and result['unique_fixtures'] == 1
    assert result['by_product']['full_game_2way']['models']['m1_projected']['roi'] == 0
    assert result['by_product']['full_game_3way']['models']['m1_projected']['roi'] == -1


def test_newer_unchanged_result_blocks_older_conflicting_correction(tmp_path, monkeypatch):
    from research.m1_service import observer
    monkeypatch.setattr(observer,'_verify_archives',lambda *a:None)
    monkeypatch.setattr(observer,'_verify_market_bytes',lambda *a:None)
    p,now=packet();recorder=Recorder(tmp_path/'m1.jsonl',test_validator,clock=lambda:now)
    recorder.capture(p)
    first=moment(p['event']['kickoff_at'])+timedelta(hours=3)
    recorder.clock=lambda:first;recorder.settle(final_result(p,first,3,1))
    recorder.clock=lambda:first+timedelta(minutes=2)
    repeated=final_result(p,first+timedelta(minutes=2),3,1)
    assert recorder.settle(repeated)[0]['type']=='result_observation'
    assert recorder.settle(repeated)==[]
    with pytest.raises(ValueError,match='backwards'):
        recorder.settle(final_result(p,first+timedelta(minutes=1),1,3))
    with pytest.raises(ValueError,match='same receipt'):
        recorder.settle(final_result(p,first+timedelta(minutes=2),1,3))
    assert recorder.summary()['settled']==1
