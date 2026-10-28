import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
import requests
import pickster_watch as w


@pytest.mark.parametrize('error', [requests.ReadTimeout, requests.ConnectionError])
def test_transient_network_failure_retries_same_public_html_once(monkeypatch, error):
    attempts, pauses, closed = [], [], []
    class Response:
        text = 'public html'
        def raise_for_status(self): pass
        def close(self): closed.append(True)
    class Session:
        def get(self, url, **kw):
            assert kw['timeout'] == (5, 30)
            attempts.append(url)
            if len(attempts) == 1: raise error('temporary')
            return Response()
    monkeypatch.setattr(w.time, 'sleep', pauses.append)
    assert w._fetch(Session(), w.SLATE_URL) == 'public html'
    assert attempts == [w.SLATE_URL, w.SLATE_URL]
    assert pauses == [1] and closed == [True]


def test_persistent_failure_is_bounded(monkeypatch):
    attempts = []
    class Session:
        def get(self, *a, **kw):
            attempts.append(1)
            raise requests.ReadTimeout('still unavailable')
    monkeypatch.setattr(w.time, 'sleep', lambda n: None)
    with pytest.raises(requests.ReadTimeout): w._fetch(Session(), w.SLATE_URL)
    assert len(attempts) == 2


def test_http_rejection_does_not_retry_and_closes_response(monkeypatch):
    calls, closed = [], []
    class Response:
        def raise_for_status(self): raise requests.HTTPError('403')
        def close(self): closed.append(True)
    class Session:
        def get(self, *a, **kw): calls.append(1); return Response()
    monkeypatch.setattr(w.time, 'sleep', lambda n: pytest.fail('not a network retry'))
    with pytest.raises(requests.HTTPError): w._fetch(Session(), w.SLATE_URL)
    assert calls == [1] and closed == [True]
