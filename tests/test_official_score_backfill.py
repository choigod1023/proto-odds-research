from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.betman_result_dom import normalize_betman_result_dom
from src.official_score_backfill import (
    apply_frozen_terms_from_official_scores, plan_frozen_terms_from_official_scores,
)
from src.live_market_refresh import offer_id, selection_id
from tests.test_betman_result_dom import document
from tests.test_live_ledger_settlement import setup_runtime
from tests.test_live_market_refresh import admissible_votes_for_offer_ledger_regressions

TIME = '2027-01-01T00:00:00Z'
SOURCE = {'name': 'synthetic_manifest', 'provenance': 'captured_betman_website_dom'}


def official(market='야구 승패', label='', result='4:2', sides=('승', '패'), game_no='9999'):
    d = document(market, label, result, sides)
    kickoff = datetime(2026, 8, 30, 18, tzinfo=ZoneInfo('Asia/Seoul'))
    d['rows'][0].update(game_name='KIA:SSG', game_no=game_no, league='KBO',
                        kickoff_text='20260830180000',
                        rowname=f'홈_원정_{int(kickoff.timestamp() * 1000)}')
    d['selectedRound'][0]['value'] = 'G101,260103'
    d['url'] = d['url'].replace('260126', '260103')
    d['received_at'] = TIME
    return normalize_betman_result_dom(d, evidence_sha256='a' * 64)['mapped_rows'][0]


def plan(records, rows, sid):
    return plan_frozen_terms_from_official_scores(records, rows, snapshot_ids=[sid],
                                                   observed_at=TIME, source=SOURCE)


def frozen(runtime, row, market, label, line, names, chosen):
    records = deepcopy(runtime.ledger.records())
    pred = records[0]
    options = []
    for name in names:
        option = {'market': market, 'label': label, 'line': line, '선택': name, '게임번호': '8888'}
        options.append({'market': market, 'label': label, 'line': line, 'selection': name,
                        'selection_id': selection_id(row, option), 'offer_id': offer_id(row, option),
                        'odds': 1.75})
    pred['features']['options'] = options
    opt = options[chosen]
    pred['predictions'].update(action='market_reference', market=market, label=label,
                               selection=opt['selection'], selection_id=opt['selection_id'],
                               offer_id=opt['offer_id'], odds=opt['odds'])
    return records, pred['snapshot_id']


def test_other_round_and_offer_keep_original_snapshot_and_price(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official()
    records = runtime.ledger.records()
    before = deepcopy(records)
    sid = records[0]['snapshot_id']
    p = plan(records, [row], sid)
    assert len(p['entries']) == 1 and p['entries'][0]['outcome']['result'] == 'hit'
    assert records == before
    entry = p['entries'][0]
    assert entry['source']['mode'] == 'official_score_with_frozen_terms'
    assert entry['source']['official_scores'][0]['official_game_no'] == '9999'
    assert entry['source']['official_scores'][0]['official_source']['evidence_sha256'] == 'a' * 64
    runtime.ledger._clock = lambda: datetime(2027, 1, 2, tzinfo=timezone.utc)
    apply_frozen_terms_from_official_scores(runtime.ledger, p, [row], observed_at=TIME, source=SOURCE)
    assert runtime.ledger.records()[0] == before[0]
    assert runtime.ledger.records()[-1]['snapshot_id'] == sid
    with pytest.raises(ValueError):
        apply_frozen_terms_from_official_scores(runtime.ledger, p, [row], observed_at=TIME, source=SOURCE)


def test_handicap_removes_source_line_then_applies_frozen_line(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official('야구 핸디캡', 'H -1.5', '0.5:2', ('승', '패'))
    records, sid = frozen(runtime, row, '핸디캡', 'H +1.5', 1.5, ('핸디홈', '핸디원정'), 0)
    p = plan(records, [row], sid)
    assert p['entries'][0]['outcome']['result'] == 'hit'
    assert p['entries'][0]['source']['original_line'] == '1.5'
    assert p['entries'][0]['source']['official_scores'][0]['official_line'] == 'H -1.5'


def test_total_uses_frozen_line_and_tie_is_held(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official('야구 언더오버', 'U/O 8.5', '8', ('언더', '오버'))
    records, sid = frozen(runtime, row, '언더오버', 'U 7.5', 7.5, ('언더', '오버'), 1)
    assert plan(records, [row], sid)['entries'][0]['outcome']['result'] == 'hit'
    records, sid = frozen(runtime, row, '언더오버', 'U 8.0', 8.0, ('언더', '오버'), 1)
    assert plan(records, [row], sid)['holds'][0]['reason'] == 'ambiguous_tie'


def test_conflicting_scores_and_source_tampering_hold(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official()
    sid = runtime.ledger.records()[0]['snapshot_id']
    conflict = official(result='2:4')
    assert plan(runtime.ledger.records(), [row, conflict], sid)['holds'][0]['reason'] == 'conflicting_official_offers'
    conflict = official(result='2:4', game_no='9998')
    assert plan(runtime.ledger.records(), [row, conflict], sid)['holds'][0]['reason'] == 'conflicting_official_scores'
    row['official_source']['result_text'] = '2:4'
    assert not plan(runtime.ledger.records(), [row], sid)['entries']


def test_scope_roster_and_latest_withhold(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official()
    records = deepcopy(runtime.ledger.records())
    sid = records[0]['snapshot_id']
    half = official('야구 전반 승무패', '', '1:0', ('승', '무', '패'))
    assert plan(records, [half], sid)['holds'][0]['reason'] == 'no_same_fixture_market_score'
    records[0]['features']['options'][1]['selection_id'] = 'sel_bad'
    assert plan(records, [row], sid)['holds'][0]['reason'] == 'invalid_frozen_roster'
    records = deepcopy(runtime.ledger.records())
    newer = deepcopy(records[0])
    newer.update(snapshot_id='later', as_of='2026-08-30T02:00:00+00:00', ledger_sequence=2)
    newer['predictions'] = {'action': 'withhold'}
    records.append(newer)
    assert plan(records, [row], sid)['holds'][0]['reason'] == 'not_latest_or_missing'


def test_frozen_line_missing_and_three_way_tie(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    row = official('야구 핸디캡', 'H -1.5', '0.5:2', ('승', '패'))
    records, sid = frozen(runtime, row, '핸디캡', 'H +0.0', 0.0,
                          ('핸디홈', '핸디무', '핸디원정'), 1)
    assert plan(records, [row], sid)['entries'][0]['outcome']['result'] == 'hit'
    for o in records[0]['features']['options']:
        o['line'] = None
    assert plan(records, [row], sid)['holds'][0]['reason'] == 'missing_frozen_line'
