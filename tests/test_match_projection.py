"""Local-only projection publication, compatibility and WAL race regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading
import zlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest
import match_api
import runtime_db
import match_projection
from match_api import MatchViews, PreparedMatchResponses, game_key, summary
from runtime_db import RuntimeDatabase


def payload(tag='old'):
    def game(date, year):
        return dict(home='홈', away='원정', year=year, date=date, round=1,
                    sport='bs', league='KBO', status='STARTED',
                    options=[{'selection_id': 'frozen', '적중': True, '배당': 1.8}],
                    prediction_record={'revision': 'frozen-revision', 'result': 'hit'},
                    decision_snapshot={'snapshot_id': 'immutable'},
                    선발={'home_detail': {'name': tag, 'history': list(range(1000))}})
    return {'generated_at': tag, 'live': [game('12.31(목) 23:00', 2026),
                                        game('01.01(금) 10:00', 2027)],
            'past': [game('10.01(목) 18:00', 2026)],
            'prediction_performance': {'large': list(range(1000))}}


@pytest.mark.parametrize('legacy', [False, True])
def test_recent_all_kst_detail_and_full_payload_equality(tmp_path, monkeypatch, legacy):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload()
    db.store_artifact('picks_v2', source)
    if legacy:
        # Simulate pre-migration DB; startup creates empty tables, never a backfill.
        with db.connect() as con:
            con.execute('DROP TABLE match_details')
            con.execute('DROP TABLE match_projection')
        db = RuntimeDatabase(db.path)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 31, 15, 1, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(match_api, 'datetime', Clock)
    monkeypatch.setattr(db, 'get_artifact_json', lambda *a: pytest.fail('full artifact read'))
    views = MatchViews(db)
    for scope in ('recent', 'all'):
        actual = views.get(scope)
        assert actual == summary(source, views.revision, scope, now=Clock.now(timezone.utc))
    assert len(views.get('recent')['live']) == 2
    assert views.get('recent')['past'] == []
    for section in ('live', 'past'):
        for game in source[section]:
            assert views.get('detail', game_key(game), views.revision)['game'] == game
    assert db.get_artifact('picks_v2') == source
    assert all(isinstance(blob, bytes) for blob in views.games.values())
    assert 'history' not in views.payload['live'][0]['선발']['home_detail']
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM match_projection').fetchone()[0] == (0 if legacy else 1)


def test_rapid_writes_and_clock_rollback_publish_matching_revisions(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'local.db')
    class Clock(datetime):
        day = 2
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, cls.day, tzinfo=timezone.utc)
    monkeypatch.setattr(runtime_db, 'datetime', Clock)
    revisions = []
    for _ in range(5):
        db.store_artifact('picks_v2', payload())
        revisions.append(db.artifact_metadata('picks_v2')['stored_at'])
        Clock.day = 1
    assert revisions == sorted(set(revisions))
    with db.connect() as con:
        assert con.execute('SELECT revision FROM match_projection').fetchone()[0] == revisions[-1]


def test_import_and_skip(tmp_path):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload('2026-10-01T00:00:00Z')
    assert db.import_artifact('picks_v2', source)
    before = db.artifact_metadata('picks_v2')['stored_at']
    assert not db.import_artifact('picks_v2', payload('2026-09-01T00:00:00Z'))
    assert db.artifact_metadata('picks_v2')['stored_at'] == before
    newer = payload('2026-10-02T00:00:00Z')
    assert db.import_artifact('picks_v2', newer)
    view = MatchViews(db).get('all')
    assert view['generated_at'] == newer['generated_at']
    with db.connect() as con:
        assert con.execute('SELECT revision FROM match_projection').fetchone()[0] == view['view']['revision']


@pytest.mark.parametrize('importing', [False, True])
def test_failed_writer_rolls_back_full_artifact_cards_details_and_revision(tmp_path, monkeypatch, importing):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload('2026-10-01T00:00:00Z')
    db.store_artifact('picks_v2', source)
    cache = PreparedMatchResponses(MatchViews(db))
    cache.refresh()
    old = cache.get_bytes()
    revision = cache.views.revision
    original = runtime_db.write_projection
    def fail(*args):
        original(*args)
        raise OSError('injected failure after projection writes')
    monkeypatch.setattr(runtime_db, 'write_projection', fail)
    with pytest.raises(OSError):
        (db.import_artifact if importing else db.store_artifact)(
            'picks_v2', payload('2026-10-02T00:00:00Z'))
    assert db.get_artifact('picks_v2') == source
    fresh = MatchViews(db)
    assert fresh.get('all') == summary(source, revision, 'all')
    assert fresh.get('detail', game_key(source['live'][0]), revision)['game'] == source['live'][0]
    cache.refresh()
    assert cache.get_bytes() == old


def test_reader_snapshot_survives_commit_between_revision_and_projection(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'local.db')
    old, new = payload(), payload('new')
    db.store_artifact('picks_v2', old)
    old_revision = db.artifact_metadata('picks_v2')['stored_at']
    original = match_api.read_projection
    def interleaved(connection, revision):
        db.store_artifact('picks_v2', new)
        return original(connection, revision)
    monkeypatch.setattr(match_api, 'read_projection', interleaved)
    views = MatchViews(db)
    assert views.get('all') == summary(old, old_revision, 'all')
    assert json.loads(zlib.decompress(views.games[game_key(old['live'][0])])) == old['live'][0]
    monkeypatch.setattr(match_api, 'read_projection', original)
    assert views.get('all')['generated_at'] == 'new'


def test_readers_cannot_see_partial_publication(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload()
    db.store_artifact('picks_v2', source)
    revision = db.artifact_metadata('picks_v2')['stored_at']
    entered, release = threading.Event(), threading.Event()
    original = runtime_db.write_projection
    def paused(*args):
        original(*args)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(runtime_db, 'write_projection', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(db.store_artifact, 'picks_v2', payload('new'))
        try:
            assert entered.wait(3)
            for _ in range(5):
                assert MatchViews(db).get('all') == summary(source, revision, 'all')
        finally:
            release.set()
        writer.result()
    assert MatchViews(db).get('all')['generated_at'] == 'new'


def test_failed_reader_preserves_published_revision(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'local.db')
    db.store_artifact('picks_v2', payload())
    cache = PreparedMatchResponses(MatchViews(db))
    cache.refresh()
    old, revision = cache.get_bytes(), cache.views.revision
    db.store_artifact('picks_v2', payload('new'))
    def fail(*args):
        raise OSError('read failure')
    monkeypatch.setattr(match_api, 'read_projection', fail)
    with pytest.raises(OSError):
        cache.refresh()
    assert cache.get_bytes() is old
    assert cache.views.revision == revision
    assert json.loads(cache.get_bytes('detail', game_key(payload()['live'][0]), revision, False))['game'] == payload()['live'][0]


def test_materialized_read_never_recompresses_and_cold_http_never_reads_db(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload()
    db.store_artifact('picks_v2', source)
    def fail(*args, **kwargs):
        pytest.fail('projection generation/full artifact read on prepared path')
    monkeypatch.setattr(match_projection, 'zlib', SimpleNamespace(compress=fail))
    monkeypatch.setattr(db, 'get_artifact_json', fail)
    cache = PreparedMatchResponses(MatchViews(db))
    connect = db.connect
    monkeypatch.setattr(db, 'connect', fail)
    with pytest.raises(KeyError):
        cache.get_bytes()
    monkeypatch.setattr(db, 'connect', connect)
    cache.refresh()
    monkeypatch.setattr(db, 'connect', fail)
    assert cache.get_bytes()
    assert cache.get_bytes('detail', game_key(source['live'][0]), cache.views.revision)


def test_old_writer_revision_mismatch_uses_read_only_fallback(tmp_path):
    db = RuntimeDatabase(tmp_path / 'local.db')
    db.store_artifact('picks_v2', payload())
    previous = db.artifact_metadata('picks_v2')['stored_at']
    source = payload('legacy-writer')
    with db.transaction() as con:
        con.execute("UPDATE artifacts SET payload_json=?,stored_at='legacy-next' WHERE name='picks_v2'",
                    (json.dumps(source),))
    views = MatchViews(db)
    assert views.get('all') == summary(source, 'legacy-next', 'all')
    assert views.get('detail', game_key(source['live'][0]), 'legacy-next')['game'] == source['live'][0]
    with db.connect() as con:
        assert con.execute('SELECT revision FROM match_projection').fetchone()[0] == previous


def test_blob_stream_retains_snapshot_across_writer_commit_and_closes(tmp_path):
    db = RuntimeDatabase(tmp_path / 'local.db')
    source = payload()
    db.store_artifact('picks_v2', source)
    revision = db.artifact_metadata('picks_v2')['stored_at']
    with db.open_artifact_json('picks_v2') as (stream, stamp):
        prefix = stream.read(11)
        db.store_artifact('picks_v2', payload('new'))
        assert stamp == revision
        assert json.loads(prefix + stream.read()) == source
    with pytest.raises(Exception, match='closed'):
        stream.read()
    with pytest.raises(KeyError):
        with db.open_artifact_json('missing'):
            pytest.fail('missing artifact yielded a stream')


@pytest.mark.parametrize('failed_scope', ['recent', 'all'])
def test_gzip_failure_keeps_scope_reply_but_rejects_old_revision_detail(tmp_path, monkeypatch, failed_scope):
    db = RuntimeDatabase(tmp_path / 'local.db')
    old, new = payload(), payload('new')
    db.store_artifact('picks_v2', old)
    cache = PreparedMatchResponses(MatchViews(db))
    with pytest.raises(KeyError):
        cache.get_bytes('all')  # Register both scopes before warming.
    cache.refresh()
    previous = {scope: cache.get_bytes(scope) for scope in ('recent', 'all')}
    old_revision = cache.views.revision
    db.store_artifact('picks_v2', new)
    compress = match_api.gzip.compress
    def fail_scope(raw, *args, **kwargs):
        if json.loads(raw).get('view', {}).get('scope') == failed_scope:
            raise OSError('injected gzip failure after view publication')
        return compress(raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(match_api.gzip, 'compress', fail_scope)
        with pytest.raises(OSError, match='injected gzip'):
            cache.refresh()
    assert cache.views.revision != old_revision
    assert cache.get_bytes(failed_scope) is previous[failed_scope]
    assert json.loads(cache.get_bytes(failed_scope, compressed=False))['view']['revision'] == old_revision
    with pytest.raises(ValueError, match='revision changed'):
        cache.get_bytes('detail', game_key(old['live'][0]), old_revision, False)
    if failed_scope == 'all':
        assert cache.get_bytes('recent') != previous['recent']
        assert json.loads(cache.get_bytes('recent', compressed=False))['view']['revision'] == cache.views.revision
    else:
        assert cache.get_bytes('all') is previous['all']
    cache.refresh()
    for scope in ('recent', 'all'):
        assert json.loads(cache.get_bytes(scope, compressed=False))['view']['revision'] == cache.views.revision
    assert json.loads(cache.get_bytes('detail', game_key(new['live'][0]), cache.views.revision, False))['game'] == new['live'][0]
