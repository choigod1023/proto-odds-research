import gzip
import http.server
import io
from types import SimpleNamespace

from deploy import supervisor
from runtime_db import RuntimeDatabase


def test_api_cache_skips_payload_read_until_revision_changes(monkeypatch):
    state = {'revision': 'one', 'reads': 0}
    def read(name):
        state['reads'] += 1
        return '{"revision":"' + state['revision'] + '"}', state['revision']
    db = SimpleNamespace(artifact_revision=lambda name: state['revision'], get_artifact_json=read)
    monkeypatch.setattr('runtime_db.RuntimeDatabase', lambda: db)
    handlers = []
    def server(address, handler):
        handlers.append(handler)
        return SimpleNamespace(serve_forever=lambda: None)
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', server)
    supervisor.serve_live()
    def request():
        handler = handlers[0].__new__(handlers[0])
        handler.path = '/api/picks'
        handler.headers = {'Accept-Encoding': 'gzip'}
        handler.wfile = io.BytesIO()
        codes = []
        handler.send_response = codes.append
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.do_GET()
        return codes[0], handler.wfile.getvalue()
    assert gzip.decompress(request()[1]) == b'{"revision":"one"}'
    request()
    assert state['reads'] == 1
    state['revision'] = 'two'
    assert gzip.decompress(request()[1]) == b'{"revision":"two"}'
    assert state['reads'] == 2
    state['revision'] = None
    assert request()[0] == 503


def test_revision_query_does_not_select_payload(tmp_path):
    db = RuntimeDatabase(tmp_path / 'cache.sqlite3')
    assert db.artifact_revision('missing') is None
    db.store_artifact('picks_v2', {'live': []})
    assert db.artifact_revision('picks_v2') == db.get_artifact_json('picks_v2')[1]
