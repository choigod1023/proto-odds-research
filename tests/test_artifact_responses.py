import gzip
import hashlib
import json
import threading
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from src.artifact_responses import ArtifactResponses
from src.runtime_db import RuntimeDatabase


def test_artifact_gzip_streams_exact_committed_bytes_without_full_text_read(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    payload = {'generated_at': 'source-time', 'live': [{'team': '삼성⚾' * 40000}]}
    db.store_artifact('picks_v2', payload)
    expected, revision = db.get_artifact_json('picks_v2')
    raw = expected.encode('utf8')
    monkeypatch.setattr(db, 'get_artifact_json', lambda _: (_ for _ in ()).throw(AssertionError('full text read')))
    cache = ArtifactResponses(db)
    body, etag = cache.get_response('picks_v2')
    assert gzip.decompress(body) == raw
    assert etag == 'W/"' + hashlib.sha256(raw).hexdigest() + '"'
    assert cache.ready['picks_v2'][0] == revision
    assert db.get_artifact_gzip('missing') is None


def test_large_rebuild_waits_for_worker_but_small_feeds_remain_available(tmp_path, monkeypatch):
    import pytest
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('picks_v2', {'generated_at': 'old'})
    db.store_artifact('live_scores', {'generated_at': 'current'})
    slot = threading.Lock()
    cache = ArtifactResponses(db, memory_lock=slot)
    with slot:
        with pytest.raises(KeyError):
            cache.get_bytes('picks_v2')
        assert json.loads(gzip.decompress(cache.get_bytes('live_scores')))['generated_at'] == 'current'
    first = cache.get_bytes('picks_v2')
    db.store_artifact('picks_v2', {'generated_at': 'new'})
    with db.connect() as connection:
        connection.execute("UPDATE artifacts SET stored_at='next' WHERE name='picks_v2'")
    original = db.get_artifact_gzip
    def guarded(name):
        assert slot.locked()
        return original(name)
    monkeypatch.setattr(db, 'get_artifact_gzip', guarded)
    with slot:
        assert cache.get_bytes('picks_v2') == first
    assert json.loads(gzip.decompress(cache.get_bytes('picks_v2')))['generated_at'] == 'new'
    assert not slot.locked()


def test_large_rebuild_failure_releases_memory_slot(tmp_path, monkeypatch):
    import pytest
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('picks_v2', {'generated_at': 'old'})
    slot = threading.Lock()
    cache = ArtifactResponses(db, memory_lock=slot)
    def fail(name):
        raise OSError('unavailable')
    monkeypatch.setattr(db, 'get_artifact_gzip', fail)
    with pytest.raises(OSError):
        cache.get_bytes('picks_v2')
    assert not slot.locked()
    assert not cache.rebuild.locked()


def test_compressed_only_and_revision_refresh(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('sample', {'generated_at': 'old', 'history': ['abc'] * 10000})
    cache = ArtifactResponses(db)
    first = cache.get_bytes('sample')
    assert len(cache.ready['sample']) == 3
    assert len(first) < len(gzip.decompress(first)) / 10
    assert cache.get_bytes('sample', compressed=False) == gzip.decompress(first)
    original = db.get_artifact_gzip
    monkeypatch.setattr(db, 'get_artifact_gzip', lambda _: (_ for _ in ()).throw(AssertionError('reread')))
    assert cache.get_bytes('sample') == first
    monkeypatch.setattr(db, 'get_artifact_gzip', original)
    db.store_artifact('sample', {'generated_at': 'new'})
    with db.connect() as c:
        c.execute("UPDATE artifacts SET stored_at='next' WHERE name='sample'")
    assert json.loads(gzip.decompress(cache.get_bytes('sample'))) == {'generated_at': 'new'}


def test_single_flight_stale_and_cold_requests(tmp_path, monkeypatch):
    import pytest
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('sample', {'generated_at': 'old'})
    cache = ArtifactResponses(db)
    first = cache.get_bytes('sample')
    with db.connect() as c:
        c.execute("UPDATE artifacts SET stored_at='next' WHERE name='sample'")
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(name):
        calls.append(name)
        entered.set()
        assert release.wait(5)
        raise OSError('read failure')
    monkeypatch.setattr(db, 'get_artifact_gzip', blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(cache.get_bytes, 'sample')
        try:
            assert entered.wait(5)
            for _ in range(20):
                assert cache.get_bytes('sample') == first
            with pytest.raises(KeyError): cache.get_bytes('cold')
            assert calls == ['sample']
        finally:
            release.set()
        assert future.result() == first
    assert cache.ready['sample'][0] != 'next'


def test_proto_labels_skip_prediction_objects(tmp_path, monkeypatch):
    from src import live_scores
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    monkeypatch.setenv('PROODD_DB_PATH', str(db.path))
    expected = {'sport': 'bs', 'date': '09.22(화) 18:00', 'home': '홈', 'away': '원정'}
    db.store_artifact('picks_v2', {'generated_at': 'old',
        'live': [dict(expected, options=[{'nested': ['x'] * 10000}])],
        'past': [expected], 'prediction_performance': {'records': ['y'] * 10000}})
    monkeypatch.setattr(live_scores, 'load_artifact', lambda *a: (_ for _ in ()).throw(AssertionError('full load')))
    assert live_scores._proto_games() == [expected, expected]
    assert RuntimeDatabase(tmp_path / 'empty.db').proto_team_labels() == []

def test_conditional_response_uses_selected_body_and_avoids_inflation(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    original = {'generated_at': 'old', 'live': [{'options': ['한글'] * 5000}]}
    db.store_artifact('picks_v2', original)
    slot = threading.Lock()
    cache = ArtifactResponses(db, memory_lock=slot)
    wire, tag = cache.get_response('picks_v2')
    assert json.loads(gzip.decompress(wire)) == original
    monkeypatch.setattr(gzip, 'decompress', lambda _: (_ for _ in ()).throw(AssertionError('unnecessary inflate')))
    assert cache.get_response('picks_v2', compressed=False, if_none_match=tag) == (None, tag)
    db.store_artifact('picks_v2', {'generated_at': 'new', 'live': []})
    with db.connect() as c:
        c.execute("UPDATE artifacts SET stored_at='next' WHERE name='picks_v2'")
    # A blocked rebuild serves the old body and its own validator, not the new DB revision.
    with slot:
        assert cache.get_response('picks_v2', if_none_match=tag) == (None, tag)
    new_wire, new_tag = cache.get_response('picks_v2', if_none_match=tag)
    assert new_wire and new_tag != tag
    assert cache.get_response('picks_v2', if_none_match='"unmatched"')[0] == new_wire

def test_live_views_keep_overnight_scores_and_isolate_detail_by_identity(tmp_path):
    import pytest
    from live_feed_views import query_key

    db = RuntimeDatabase(tmp_path / 'runtime.db')
    overnight = {'game_id': 'named:1', 'source': 'named', 'start': '2026-11-06T23:00:00+09:00',
                 'status': 'STARTED', 'home_alias': ['삼성'], 'home_score': 3, 'observed_at': '2026-11-07T00:05:00+09:00',
                 'timeline': [{'type': 'GOAL', 'text': '홈 득점'}], 'timeline_scope': 'latest',
                 'period_scores': [{'home': 3, 'away': 0}]}
    next_day = dict(overnight, start='2026-11-07T23:00:00+09:00', home_score=1, timeline=[])
    doc = {'generated_at': overnight['observed_at'], 'partial': True, 'games': [overnight, next_day]}
    db.store_artifact('live_scores', doc)
    cache = ArtifactResponses(db)
    body, tag = cache.get_response('live_scores', compressed=False, view='summary')
    result = json.loads(body)
    assert result['summary'] and result['partial']
    assert [(row['start'], row['home_score']) for row in result['games']] == [
        (overnight['start'], 3), (next_day['start'], 1)]
    assert result['games'][0]['observed_at'] == overnight['observed_at']
    assert all('timeline' not in row and 'period_scores' not in row for row in result['games'])
    assert cache.get_response('live_scores', if_none_match=tag, view='summary') == (None, tag)
    key = query_key('live_scores', 'detail', {f: [overnight[f]] for f in ('game_id', 'source', 'start')})
    body, _ = cache.get_response('live_scores', view='detail', key=key, compressed=False)
    assert json.loads(body) == {'identity': list(key), 'detail': {
        field: overnight[field] for field in ('timeline', 'timeline_scope', 'period_scores')
    }}
    assert json.loads(cache.get_bytes('live_scores', compressed=False)) == doc
    db.store_artifact('live_scores', dict(doc, games=[overnight, overnight]))
    body, _ = cache.get_response('live_scores', view='detail', key=key, compressed=False)
    assert json.loads(body)['detail'] is None
    with pytest.raises(ValueError):
        query_key('live_scores', 'detail', {'game_id': ['other', 'named:1'], 'source': ['named'], 'start': ['today']})


def test_odds_summary_and_history_use_separate_cache_entries(tmp_path):
    import pytest
    from live_feed_views import query_key

    db = RuntimeDatabase(tmp_path / 'runtime.db')
    doc = {'odds': {'132': {'3': [1.2, 3.2]}}, 'markets': {'132': {'3': {'game_no': '3'}}},
           'history': {'132': {'3': [{'odds': [1.3, 3.1]}], '4': [{'odds': [1.5, 2.5]}]},
                       '133': {'3': [{'odds': [1.9, 1.9]}]}}}
    db.store_artifact('live_odds', doc)
    cache = ArtifactResponses(db)
    body, _ = cache.get_response('live_odds', compressed=False, view='summary')
    assert json.loads(body) == {key: doc[key] for key in ('odds', 'markets')}
    key = query_key('live_odds', 'detail', {'round': ['132'], 'game_nos': ['3,5,3']})
    body, _ = cache.get_response('live_odds', compressed=False, view='detail', key=key)
    assert json.loads(body) == {'round': '132', 'history': {'3': doc['history']['132']['3']}}
    with pytest.raises(ValueError):
        query_key('live_odds', 'detail', {'round': ['132'], 'game_nos': ['3,4".x']})
    for number in range(30):
        cache.get_response('live_odds', view='detail', key=(str(number), '3'))
    assert len(cache.details) == 24
    assert ('live_odds', 'summary', None) in cache.ready


def test_player_display_reads_first_fixture_and_latest_names(tmp_path):
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    rows = [
        {'gameId': 'game', 'field': 'homeStarterName', 'value': '최초', 'observed_at': '01'},
        {'gameId': 'game', 'field': 'homeStarterName', 'value': '중간', 'observed_at': '02'},
        {'gameId': 'game', 'field': 'awayStarterName', 'value': '원정', 'observed_at': '03'},
        {'gameId': 'game', 'field': 'homeStarterName', 'value': '최신', 'observed_at': '04'},
        {'gameId': 'game', 'field': 'homeStarterName', 'value': '정정', 'observed_at': '04'},
        {'gameId': 'game', 'field': 'other', 'value': '별도', 'observed_at': '05'},
    ]
    db.append_events('starter_announcements', rows)
    assert [row['value'] for row in db.starter_display_events()] == ['최초', '원정', '정정']
    assert db.events('starter_announcements') == rows
