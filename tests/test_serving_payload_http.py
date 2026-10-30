import gzip
import http.client
import http.server
import json
import threading
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deploy import supervisor
import runtime_db


def test_picks_conditional_http_and_cors(tmp_path, monkeypatch):
    db = runtime_db.RuntimeDatabase(tmp_path / 'fixture.db')
    fixture = {'generated_at': 'original', 'live': [{'home': '홈', 'options': list(range(1000))}]}
    db.store_artifact('picks_v2', fixture)
    monkeypatch.setattr(runtime_db, 'RuntimeDatabase', lambda: db)
    monkeypatch.setattr(supervisor, 'warm_match_views', lambda *args: None)
    ready = threading.Event()
    holder = {}
    original_server = http.server.ThreadingHTTPServer
    class LocalServer(original_server):
        def __init__(self, _address, handler):
            super().__init__(('127.0.0.1', 0), handler)
            holder['server'] = self
            ready.set()
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', LocalServer)
    worker = threading.Thread(target=supervisor.serve_live, daemon=True)
    worker.start()
    assert ready.wait(5)
    server = holder['server']
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        conn.request('OPTIONS', '/api/picks', headers={'Origin':'https://example.test', 'Access-Control-Request-Headers':'if-none-match,cache-control'})
        result = conn.getresponse(); result.read()
        assert result.status == 204 and 'if-none-match' in result.getheader('Access-Control-Allow-Headers').lower()
        conn.request('GET', '/api/picks?_=1', headers={'Accept-Encoding':'gzip'})
        result = conn.getresponse(); body = result.read(); etag = result.getheader('ETag')
        assert result.status == 200 and result.getheader('Content-Encoding') == 'gzip'
        assert result.getheader('Access-Control-Expose-Headers') == 'ETag'
        assert json.loads(gzip.decompress(body)) == fixture
        conn.request('GET', '/api/picks?_=2', headers={'If-None-Match':etag})
        result = conn.getresponse()
        assert result.status == 304 and result.read() == b'' and result.getheader('ETag') == etag
        db.store_artifact('picks_v2', {'generated_at':'new', 'live':[]})
        with db.connect() as c:
            c.execute("UPDATE artifacts SET stored_at='next' WHERE name='picks_v2'")
        conn.request('GET', '/api/picks?_=3', headers={'If-None-Match':etag})
        result = conn.getresponse(); new_body = result.read()
        assert result.status == 200 and result.getheader('ETag') != etag
        assert json.loads(new_body)['generated_at'] == 'new'
    finally:
        conn.close(); server.shutdown(); worker.join(5)
    assert not worker.is_alive()
