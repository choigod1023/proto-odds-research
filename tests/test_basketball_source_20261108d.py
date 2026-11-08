import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'experiments/basketball-source-20261108d/extract.py'
spec = importlib.util.spec_from_file_location('basketball_extract', path)
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


def sample():
    common = {'league': 'KBL', 'kickoff': '2025-01-01T14:00:00+09:00', 'home': 'A', 'away': 'B',
              'body_sha256': 'fixturehash'}
    fixture = dict(common, kind='fixture', payload={'source_fixture_id': extractor.GAME,
                       'home_team_id': '06', 'away_team_id': '60'})
    result = dict(common, kind='result', payload={'source_fixture_id': extractor.GAME,
                     'home_score': 10, 'away_score': 7})
    rows = [{'player': {'pcode': 'one', 'tcode': '06'}, 'records': {'playMin': 10, 'playSec': 5, 'score': 10}},
            {'player': {'pcode': 'two', 'tcode': '60'}, 'records': {'playMin': 10, 'playSec': 7, 'score': 7}}]
    body = json.dumps(rows).encode()
    meta = {'status': 200, 'body_sha256': hashlib.sha256(body).hexdigest(),
            'timestamp_basis': 'http_response_body_completed', 'received_at': '2026-11-08T00:00:00Z',
            'url': f'https://api.kbl.or.kr/match/{extractor.GAME}/player-stat'}
    return meta, body, [fixture, result]


def test_verified_box_preserves_ids_and_actual_receipt():
    meta, body, fixtures = sample()
    x = extractor.extract(meta, body, fixtures)
    assert x['availability'] == 'postgame_only'
    assert x['received_at'] == meta['received_at']
    assert x['players'][0]['player_id'] == 'one'
    assert x['team_totals']['06']['seconds'] == 605


def test_hash_identity_and_score_conflicts_fail_closed():
    meta, body, fixtures = sample()
    with pytest.raises(ValueError):
        extractor.extract(meta, body+b' ', fixtures)
    with pytest.raises(ValueError):
        extractor.extract(dict(meta, url=meta['url'].replace(extractor.GAME, 'other')), body, fixtures)
    wrong = copy.deepcopy(fixtures)
    wrong[1]['payload']['home_score'] = 9
    with pytest.raises(ValueError):
        extractor.extract(meta, body, wrong)


def test_missing_receipt_or_duplicate_fixture_fails_closed():
    meta, body, fixtures = sample()
    with pytest.raises(ValueError):
        extractor.extract(dict(meta, timestamp_basis='unknown'), body, fixtures)
    with pytest.raises(ValueError):
        extractor.extract(dict(meta, received_at='2026-11-08T00:00:00'), body, fixtures)
    with pytest.raises(ValueError):
        extractor.extract(meta, body, fixtures + [fixtures[0]])
