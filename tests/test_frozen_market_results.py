"""Synthetic changed-line regressions, not measured betting performance."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ai_decision import event_id, offer_id, selection_id
from frozen_market_results import add_frozen_candidates, settlement_source
from multi_market_recommendations import result_for_offer
from runtime_db import RuntimeDatabase
from runtime_datasets import RESULT_INSERT

OBSERVED = datetime(2026, 11, 6, 13, tzinfo=timezone.utc)


def case(tmp_path, *, total=False):
    fixture = dict(year=2026, round=132, date='11.06(금) 19:00', sport='bk',
                   league='WKBL', home='하나은행', away='KB스타즈')
    family, label, line, side, number = ('언더오버', 'U 142.5', 142.5, '언더', '2855') if total else (
        '핸디캡', 'H -2.5', -2.5, '핸디홈', '2854')
    option = dict(market=family, label=label, line=line, 선택=side, 게임번호=number)
    item = dict(market=family, label=label, line=line, selection=side, game_no=number,
                odds=1.72, model='market-baseline-v1', selection_id=selection_id(fixture, option), offer_id=offer_id(fixture, option),
                settlement_rule='total_half' if total else 'handicap_half', period='full_game',
                result_period='full_including_extra')
    item['terms'] = dict(game_no=number, league='WKBL', sport='bk', unit='points', n_way=2,
                         line=str(line), market='total' if total else 'handicap',
                         settlement_rule=item['settlement_rule'], period='full_including_extra')
    prediction = dict(features={'fixture': fixture}, kickoff='2026-11-06T10:00:00+00:00',
                      as_of='2026-11-06T09:12:44+00:00', captured_at='2026-11-06T09:14:10+00:00',
                      market_observed_at='2026-11-06T09:12:44+00:00',
                      predictions={'market_recommendations': {'items': [item]}})
    event = event_id(fixture)
    row = dict(fixture, game_no=number, market=family, n_way=2,
               label='U 143.5' if total else 'H -3.5', result='언더' if total else '핸디패')
    feed = dict(markets={'132': {number: row}}, generated_at=OBSERVED.isoformat())
    # Existing official final products establish this event's eligible scope.
    candidates = {(event, 'current-selection', 'current-offer'): [('miss', row, '132')]}
    database = RuntimeDatabase(tmp_path / 'db.sqlite3')
    # Home wins by three: old -2.5 wins but closing -3.5 loses.
    score = ['proto', 2026, 132, '2852', '2026-11-06T19:00:00', 'WKBL', 'bk',
             '하나은행', 'KB스타즈', 73, 70, '2026-11-06T12:02:05+00:00', 'confirmed']
    insert(database, score)
    return database, fixture, item, prediction, feed, candidates, score


def insert(db, row):
    with db.transaction() as connection:
        connection.execute(RESULT_INSERT, row)


def run(data):
    db, fixture, item, prediction, feed, candidates, _ = data
    count = add_frozen_candidates(candidates, feed, {event_id(fixture): prediction}, OBSERVED, database=db)
    return count, candidates.get((event_id(fixture), item['selection_id'], item['offer_id']))


def test_changed_handicap_uses_original_line_and_price_not_closing_grade(tmp_path):
    data = case(tmp_path)
    prediction = deepcopy(data[3])
    count, matches = run(data)
    assert count == 1
    status, row, rnd = matches[0]
    assert status == 'hit' and 'result' not in row
    assert float(result_for_offer(data[2], status)['net_units']) == pytest.approx(.72)
    source = settlement_source(row, rnd)
    assert source['original_line'] == -2.5 and source['final_score'] == [73, 70]
    assert source['result_feed_label'] == 'H -3.5' and len(source['score_sha256']) == 64
    assert data[3] == prediction
    assert run(data)[0] == 0  # Existing candidates are never overwritten.


def test_changed_total_does_not_copy_new_line_win(tmp_path):
    data = case(tmp_path, total=True)
    count, matches = run(data)
    assert count == 1 and matches[0][0] == 'miss'  # 143 > 142.5, despite closing under win.
    assert result_for_offer(data[2], matches[0][0])['net_units'] == '-1'


@pytest.mark.parametrize('failure', [
    'missing_score', 'dataset_only', 'unconfirmed', 'future_score', 'reversed_teams',
    'changed_kickoff', 'conflicting_reissue', 'missing_terms', 'wrong_period', 'soccer',
    'different_product', 'duplicate_product', 'live', 'void', 'late_capture', 'wrong_id',
    'nan_price', 'unchanged_line', 'closing_grade_conflict', 'future_item_price',
])
def test_incomplete_or_conflicting_evidence_holds(tmp_path, failure):
    data = case(tmp_path)
    db, fixture, item, prediction, feed, candidates, score = data
    row = feed['markets']['132']['2854']
    if failure in {'missing_score', 'dataset_only'}:
        with db.transaction() as connection:
            connection.execute('DELETE FROM match_results')
        if failure == 'dataset_only':
            score[0] = 'dataset'; insert(db, score)
    elif failure in {'unconfirmed', 'future_score', 'reversed_teams', 'changed_kickoff'}:
        score[11] = '2026-11-06T12:30:00+00:00'
        if failure == 'unconfirmed':
            score[4] = '0001-01-01T00:00:00'; score[7:9] = ['', '']; score[12] = 'unconfirmed'
        elif failure == 'future_score': score[11] = '2026-11-06T14:00:00+00:00'
        elif failure == 'reversed_teams': score[7:9] = score[7:9][::-1]
        else: score[4] = '2026-11-06T19:30:00'
        insert(db, score)
    elif failure == 'conflicting_reissue':
        score[2] = 131; score[9] = 74; insert(db, score)
    elif failure == 'missing_terms': item.pop('terms')
    elif failure == 'wrong_period': item['terms']['period'] = 'regulation'
    elif failure == 'soccer': fixture['sport'] = 'sc'
    elif failure == 'different_product': row['game_no'] = '9999'
    elif failure == 'duplicate_product': feed['markets']['132']['duplicate'] = deepcopy(row)
    elif failure == 'live': row['result'] = '진행중'
    elif failure == 'void': row['result'] = '취소'
    elif failure == 'late_capture': prediction['captured_at'] = '2026-11-06T09:31:00+00:00'
    elif failure == 'wrong_id': item['offer_id'] = 'wrong'
    elif failure == 'nan_price': item['odds'] = float('nan')
    elif failure == 'unchanged_line': row['label'] = 'H -2.5'
    elif failure == 'closing_grade_conflict': row['result'] = '핸디승'
    elif failure == 'future_item_price': item['market_observed_at'] = '2026-11-06T09:31:00+00:00'
    assert run(data) == (0, None)


def test_latest_score_correction_and_identical_reissue(tmp_path):
    data = case(tmp_path)
    db, fixture, item, prediction, feed, candidates, score = data
    score[2] = 131; insert(db, score)
    assert run(data)[1][0][0] == 'hit'
    # Both source products now agree on the corrected score; original -2.5 loses.
    for rnd in (131, 132):
        score[2] = rnd; score[9:11] = [69, 74]; score[11] = '2026-11-06T12:30:00+00:00'
        insert(db, score)
    candidates.pop((event_id(fixture), item['selection_id'], item['offer_id']))
    assert run(data)[1][0][0] == 'miss'


def test_database_failure_does_not_break_existing_candidates(tmp_path, monkeypatch):
    import sqlite3
    data = case(tmp_path)
    before = deepcopy(data[5])
    monkeypatch.setattr(data[0], 'connect', lambda: (_ for _ in ()).throw(sqlite3.OperationalError('busy')))
    assert run(data) == (0, None)
    assert data[5] == before


def test_pipeline_appends_market_and_featured_grades_without_rewriting_pick(tmp_path, monkeypatch):
    from live_market_refresh import refresh_document, record_live_market_revisions, settle_live_market_results
    from prediction_runtime import PredictionRuntime
    import runtime_db

    data = case(tmp_path)
    db, fixture, item, prediction, final_feed, candidates, score = data
    monkeypatch.setattr(runtime_db, 'database_enabled', lambda: True)
    monkeypatch.setattr(runtime_db, 'configured_path', lambda: db.path)
    pregame = deepcopy(final_feed)
    pregame['generated_at'] = prediction['as_of']
    pregame['markets']['132']['2854'].update(label='H -2.5', odds=[1.72, 1.76], result='경기전')
    pregame['markets']['132']['2852'] = dict(fixture, game_no='2852', market='승패',
        label='', n_way=2, odds=[1.49, 2.23], result='경기전')
    document, _ = refresh_document({'live': [], 'past': []}, pregame)
    game = document['live'][0]
    for option in game['options']:
        if option['market'] == '핸디캡':
            option.update(period='full_game', settlement_scope='handicap_half')
    item['automatic_recommendation'] = True
    game['market_recommendations'] = {'version': 'multi-market-recommendations-v1', 'items': [item]}
    clock = [datetime.fromisoformat(prediction['captured_at'])]
    runtime = PredictionRuntime(tmp_path / 'ledger.jsonl', clock=lambda: clock[0])
    # Capture clock is genuinely before T-30; settlement later uses the same ledger.
    record_live_market_revisions(document, pregame['generated_at'], runtime)
    original = deepcopy(runtime.records()[0])
    event = event_id(fixture)
    assert runtime.featured_records()[event]['pick']['odds'] == 1.72
    assert runtime.featured_records()[event]['result'] is None
    clock[0] = OBSERVED
    monkeypatch.setattr(runtime_db, 'database_enabled', lambda: False)
    assert settle_live_market_results(final_feed, runtime) == 0
    assert runtime.featured_records()[event]['result'] is None
    monkeypatch.setattr(runtime_db, 'database_enabled', lambda: True)
    assert settle_live_market_results(final_feed, runtime) == 1
    assert settle_live_market_results(final_feed, runtime) == 0
    assert runtime.records()[0] == original
    grade = runtime.featured_records()[event]['result']
    assert grade['result'] == 'hit' and grade['net_units'] == '0.72'
    assert grade['settlement_basis'] == 'frozen_line_final_score'
    assert 'official_result' not in grade
    assert grade['source']['final_score'] == [73, 70]
    market = runtime.market_records()[event]['market_recommendation_results'][item['offer_id']]
    assert market['result'] == 'hit' and market['odds'] == '1.72'


def test_retained_wkbl_payload_replay_original_minus_2_5_misses(tmp_path):
    fixture = json.loads((Path(__file__).parent / 'fixtures/frozen_line_wkbl_20261106.json').read_text(encoding='utf-8'))
    db = RuntimeDatabase(tmp_path / 'replay.db')
    with db.transaction() as connection:
        connection.executemany(RESULT_INSERT, fixture['score_rows'])
    prediction, feed = fixture['prediction'], fixture['feed']
    event = event_id(prediction['features']['fixture'])
    candidates = {(event, 'closing', 'closing'): [('miss', feed['markets']['132']['2854'], '132')]}
    original = deepcopy(prediction)
    assert add_frozen_candidates(candidates, feed, {event: prediction},
        datetime.fromisoformat(feed['generated_at']), database=db) == 1
    item = prediction['predictions']['market_recommendations']['items'][0]
    status, row, rnd = candidates[(event, item['selection_id'], item['offer_id'])][0]
    assert status == 'miss' and result_for_offer(item, status)['net_units'] == '-1'
    assert item['line'] == -2.5 and item['odds'] == 1.72 and prediction == original
    assert settlement_source(row, rnd)['final_score'] == [69, 74]
