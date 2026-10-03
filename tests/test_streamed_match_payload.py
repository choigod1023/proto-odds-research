import gzip
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runtime_db import RuntimeDatabase
from match_api import MatchViews, game_key, summary
from artifact_responses import ArtifactResponses
from streamed_match_payload import projected_payload


def test_projection_preserves_unicode_numbers_metadata_and_skips_ledger():
    payload = {'generated_at': 'old', 'meta': {'nested': [None, True, 1.25, '한글']},
               'live': [{'home': '홈', 'away': '원정', 'odds': 1.49}], 'past': [],
               'prediction_performance': {'large': ['irrelevant']*10000}}
    result = projected_payload(io.BytesIO(json.dumps(payload, ensure_ascii=False).encode()), lambda x: x)
    assert result == {k: v for k, v in payload.items() if k != 'prediction_performance'}


@pytest.mark.parametrize('raw', [b'[]', b'{', b'{"live":[1]}', b'{"live":null}',
                                 b'{"live":[{}]', b'{} garbage', b'{}{}'])
def test_malformed_stream_is_rejected(raw):
    with pytest.raises((ValueError, StopIteration)):
        projected_payload(io.BytesIO(raw), lambda x: x)


def test_blob_snapshot_revision_and_closure(tmp_path):
    db = RuntimeDatabase(tmp_path/'runtime.db')
    db.store_artifact('sample', {'generated_at': 'old', '한글': '내용'})
    with db.connect() as connection:
        connection.execute("UPDATE artifacts SET stored_at='old-revision' WHERE name='sample'")
    with db.open_artifact_json('sample') as (stream, stamp):
        db.store_artifact('sample', {'generated_at': 'new'})
        assert json.loads(stream.read())['generated_at'] == 'old'
        assert stamp != db.artifact_metadata('sample')['stored_at']
    with pytest.raises(sqlite3.ProgrammingError):
        stream.read(1)
    with pytest.raises(KeyError):
        with db.open_artifact_json('missing'):
            pass


def test_failure_keeps_old_view_and_wire_revision(tmp_path):
    db = RuntimeDatabase(tmp_path/'runtime.db')
    game = {'home': '홈', 'away': '원정', 'date': '10.01(목) 10:00', 'year': 2026,
            'options': [{'selection_id': 'h', '배당': 1.49}], 'prediction_record': {'frozen': True}}
    original = {'generated_at': 'old', 'live': [game], 'past': []}
    db.store_artifact('picks_v2', original)
    views = MatchViews(db)
    response = views.get('all')
    stamp = response['view']['revision']
    with db.connect() as connection:
        connection.execute("UPDATE artifacts SET payload_json=?, stored_at='bad' WHERE name='picks_v2'", ('{"live":[{}',))
    with pytest.raises(ValueError):
        views.get('all')
    assert views.revision == stamp
    assert json.loads(__import__('zlib').decompress(views.games[game_key(game)])) == game
    assert response == summary(original, stamp, 'all')


def test_streaming_paths_never_load_full_json(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path/'runtime.db')
    db.store_artifact('picks_v2', {'generated_at': 'old', 'live': [], 'past': []})
    monkeypatch.setattr(db, 'get_artifact_json', lambda *a: pytest.fail('full JSON allocation'))
    views = MatchViews(db)
    assert views.get('all')['generated_at'] == 'old'
    assert json.loads(gzip.decompress(ArtifactResponses(db).get_bytes('picks_v2')))['generated_at'] == 'old'
