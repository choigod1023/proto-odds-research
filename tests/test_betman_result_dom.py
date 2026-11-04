from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from src.betman_result_dom import normalize_betman_result_dom as _normalize
from src.bets import winner_index
from src.official_result_backfill import apply_backfill, plan_backfill
from tests.test_live_ledger_settlement import setup_runtime
from tests.test_live_market_refresh import admissible_votes_for_offer_ledger_regressions


def normalize_betman_result_dom(document):
    return _normalize(document, evidence_sha256='a' * 64)


def document(market='축구 승무패', label='', result='1:0', sides=('승', '무', '패')):
    kickoff = datetime(2026, 10, 23, 9, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    keys = ['1', '3'] if len(sides) == 2 else ['1', '2', '3']
    row = {'game_name': '플라멩구:에스라플', 'home_name': '원본홈', 'away_name': '원본원정',
           'game_no': '9986', 'league': '코파리베', 'kickoff_text': '20261023093000',
           'rowname': f'원본홈_원본원정_{int(kickoff.timestamp() * 1000)}',
           'market_text': market, 'label': label, 'result_action': '결과보기',
           'result_text': result,
           'options': [{'key': key, 'text': '팀 vs 팀\n' + side + '\n1.50'} for key, side in zip(keys, sides)]}
    return {'url': 'https://www.betman.co.kr/main/mainPage/gamebuy/closedGameSlipIFR.do?gmId=G101&gmTs=260126',
            'selectedRound': [{'value': 'G101,260126'}], 'received_at': '2026-11-04T02:31:41.210Z', 'rows': [row]}


def mapped(*args):
    return normalize_betman_result_dom(document(*args))['mapped_rows'][0]


def test_fixture_and_provenance():
    x = normalize_betman_result_dom(document())
    row = x['mapped_rows'][0]
    assert (row['sport'], row['year'], row['round'], row['game_no']) == ('sc', 2026, 126, '9986')
    assert (row['home'], row['away'], row['date']) == ('플라멩구', '에스라플', '10.23(금) 09:30')
    assert (row['market'], row['label'], row['n_way'], row['result']) == ('승무패', '', 3, '홈승')
    assert row['dom_evidence'] == x['source']
    assert x['source']['received_at'] == '2026-11-04T02:31:41.210000+00:00'
    assert len(x['source']['document_sha256_canonical_json']) == 64
    assert row['official_source']['evidence_sha256'] == 'a' * 64
    assert row['official_source']['result_text'] == '1:0'


@pytest.mark.parametrize('market,label,score,sides,expected', [
    ('야구 승패', '', '1:2', ('승', '패'), ('승패', '', '홈패')),
    ('축구 전반 승무패', '', '0:0', ('승', '무', '패'), ('전반승무패', 'h(전반)', '무승부')),
    ('축구 핸디캡', 'H -1.0', '-1:0', ('승', '무', '패'), ('핸디캡', 'H -1.0', '핸디패')),
    ('축구 전반 핸디캡', 'H +1.0', '1:1', ('승', '무', '패'), ('전반핸디캡', 'h H +1.0', '핸디무')),
    ('축구 소수핸디캡', 'H -1.5', '0.5:1', ('승', '패'), ('핸디캡', 'H -1.5', '핸디패')),
    ('배구 세트핸디캡', 'H -2.5', '0.5:0', ('승', '패'), ('핸디캡', 'H -2.5', '핸디승')),
    ('축구 언더오버', 'U/O 2.5', '2', ('언더', '오버'), ('언더오버', 'U 2.5', '언더')),
    ('축구 전반 언더오버', 'U/O 1.5', '2', ('언더', '오버'), ('전반언더오버', 'h U 1.5', '오버')),
    ('야구 승1패', '', '3:2', ('승', '1', '패'), ('승①패', '승①패', '①')),
    ('농구 승5패', '', '80:85', ('승', '5', '패'), ('승⑤패', '', '⑤')),
])
def test_market_mapping(market, label, score, sides, expected):
    row = mapped(market, label, score, sides)
    assert (row['market'], row['label'], row['result']) == expected
    assert row['raw_market_scope'] == market
    assert winner_index(row['n_way'], row['result']) is not None


@pytest.mark.parametrize('market,side,score,result', [
    ('야구 승1패', '1', '3:2', '①'), ('야구 승1패', '1', '4:2', '홈승'),
    ('야구 승1패', '1', '2:4', '홈패'), ('농구 승5패', '5', '90:95', '⑤'),
    ('농구 승5패', '5', '96:90', '홈승'), ('농구 승5패', '5', '90:96', '홈패'),
])
def test_margin_boundaries(market, side, score, result):
    assert mapped(market, '', score, ('승', side, '패'))['result'] == result


@pytest.mark.parametrize('mutate,reason', [
    (lambda r: r.update(result_action=''), 'not_completed'),
    (lambda r: r.update(result_text='취소'), 'invalid_result'),
    (lambda r: r.update(result_text='-'), 'invalid_result'),
    (lambda r: r.update(rowname='team_1792715400001'), 'invalid_kickoff'),
    (lambda r: r.update(game_name='missing-colon'), 'invalid_fixture'),
    (lambda r: r['options'][0].update(key='3'), 'invalid_options'),
    (lambda r: r['options'][1].update(text='팀 vs 팀\n패\n2.00'), 'invalid_options'),
    (lambda r: r.update(market_text='축구 SUM'), 'unsupported_market'),
])
def test_rejection(mutate, reason):
    d = document()
    mutate(d['rows'][0])
    result = normalize_betman_result_dom(d)
    assert not result['mapped_rows'] and result['rejected_counts'] == {reason: 1}
    assert result['rejected'][0]['row'] == d['rows'][0]


def test_quarter_handicap_and_ties_hold():
    for d, reason in [
        (document('축구 소수핸디캡', 'H +0.25', '1:0', ('승', '패')), 'unsupported_fractional_handicap'),
        (document('축구 언더오버', 'U/O 2.0', '2', ('언더', '오버')), 'ambiguous_tie'),
        (document('야구 승1패', '', '2:2', ('승', '1', '패')), 'ambiguous_tie'),
        (document('농구 승패', '', '2:2', ('승', '패')), 'ambiguous_tie'),
    ]:
        assert normalize_betman_result_dom(d)['rejected_counts'] == {reason: 1}


@pytest.mark.parametrize('change', [
    lambda d: d.update(url=d['url'].replace('260126', '260125')),
    lambda d: d.update(selectedRound=[{'value': 'G101,260125'}]),
    lambda d: d.update(url=d['url'].replace('www.betman.co.kr', 'example.com')),
    lambda d: d.update(received_at='2026-11-04T02:31:41'),
])
def test_document_integrity(change):
    d = document()
    change(d)
    with pytest.raises(ValueError):
        normalize_betman_result_dom(d)


def test_normalized_row_matches_backfill_contract(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path)
    d = document('야구 승패', '', '4:2', ('승', '패'))
    kickoff = datetime(2026, 8, 30, 18, tzinfo=ZoneInfo('Asia/Seoul'))
    d['rows'][0].update(game_name='KIA:SSG', game_no='7100', league='KBO',
                        kickoff_text='20260830180000',
                        rowname=f'홈_원정_{int(kickoff.timestamp() * 1000)}')
    d['selectedRound'][0]['value'] = 'G101,260102'
    d['url'] = d['url'].replace('260126', '260102')
    d['received_at'] = '2027-01-01T00:00:00Z'
    normalized = normalize_betman_result_dom(d)
    sid = runtime.ledger.records()[0]['snapshot_id']
    plan = plan_backfill(runtime.ledger.records(), normalized['mapped_rows'],
                         snapshot_ids=[sid], observed_at=d['received_at'], source=normalized['source'])
    assert len(plan['entries']) == 1
    assert plan['entries'][0]['outcome']['result'] == 'hit'
    evidence = plan['entries'][0]['source']['official_source']
    assert evidence == normalized['mapped_rows'][0]['official_source']
    assert evidence['result_text'] == '4:2' and evidence['evidence_sha256'] == 'a' * 64
    for field, value in [('evidence_sha256', 'bad'), ('result_text', '0:2'),
                         ('url', 'https://example.com/'), ('received_at', 'bad'),
                         ('kickoff_text', '20260830190000'), ('result_action', ''),
                         ('game_name', 'other:SSG')]:
        altered = deepcopy(normalized['mapped_rows'])
        altered[0]['official_source'][field] = value
        held = plan_backfill(runtime.ledger.records(), altered, snapshot_ids=[sid],
                             observed_at=d['received_at'], source=normalized['source'])
        assert not held['entries'] and held['holds'][0]['reason'] == 'invalid_official_source'
        with pytest.raises(ValueError):
            apply_backfill(runtime.ledger, plan, altered, observed_at=d['received_at'], source=normalized['source'])
    altered = deepcopy(normalized['mapped_rows'])
    altered[0]['result'] = '홈패'
    held = plan_backfill(runtime.ledger.records(), altered, snapshot_ids=[sid],
                         observed_at=d['received_at'], source=normalized['source'])
    assert held['holds'][0]['reason'] == 'invalid_official_source'
    runtime.ledger._clock = lambda: datetime(2027, 1, 2, tzinfo=timezone.utc)
    apply_backfill(runtime.ledger, plan, normalized['mapped_rows'],
                   observed_at=d['received_at'], source=normalized['source'])
    assert runtime.ledger.records()[-1]['source']['official_source'] == evidence


@pytest.mark.parametrize(('module', 'root'), [
    ('src.betman_result_dom', True), ('betman_result_dom', False),
    ('src.official_result_backfill', True), ('official_result_backfill', False),
])
def test_import_from_cli_working_directory(tmp_path, module, root):
    project = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PYTHONPATH': str(project if root else project / 'src')}
    subprocess.run([sys.executable, '-c', f'import {module}'], cwd=tmp_path,
                   env=env, check=True, capture_output=True, text=True)


def test_capture_file_hash_is_required():
    for sha in ('bad', 'A' * 64, None):
        with pytest.raises(ValueError):
            _normalize(document(), evidence_sha256=sha)
