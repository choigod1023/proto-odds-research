import pytest
from scripts.capture_tipster_forecasts import parse, validate_url, read

URL = 'https://www.sportsmole.co.uk/football/preview/home-vs-away_123.html'


def test_score_is_not_probability_or_fact():
    html = b'<h1>Home vs Away</h1><h2>We say: Home 2-1 Away</h2>'
    result = parse(html, URL, '2026-10-25T00:00:00Z')
    assert result['forecasts'][0]['outcome'] == 'home'
    assert result['forecasts'][0]['probability'] is None
    assert result['temporal_eligibility'] == 'unverified'
    assert result['affects_production'] is False


def test_draw_and_ignore_other_text():
    html = b'<h1>Home vs Away</h1><p>We say: Home 9-0 Away</p><h2>We say: Home 1-1 Away</h2>'
    result = parse(html, URL, '2026-10-25T00:00:00Z')
    assert len(result['forecasts']) == 1
    assert result['forecasts'][0]['outcome'] == 'draw'


def test_wrong_fixture():
    html = b'<h1>Home vs Away</h1><h2>We say: Other 2-0 Away</h2>'
    assert not parse(html, URL, '2026-10-25T00:00:00Z')['forecasts']


@pytest.mark.parametrize('url', ['http://www.sportsmole.co.uk/preview/a.html',
    URL+'?secret=x', URL+'#x', URL.replace('www.', 'evil.'),
    URL.replace('https://','https://user:password@'), URL.replace('.co.uk','.co.uk:123')])
def test_url_restrictions(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_naive_timestamp():
    with pytest.raises(ValueError):
        parse(b'', URL, '2026-10-25T00:00:00')


def test_no_redirect_or_large_response():
    class Response:
        status_code = 302
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, _): return [b'1234']
    class Session:
        def get(self, *args, **kwargs):
            assert kwargs['allow_redirects'] is False
            return Response()
    with pytest.raises(ValueError, match='http_302'):
        read(Session(), URL, 3)
    Response.status_code = 200
    with pytest.raises(ValueError, match='too large'):
        read(Session(), URL, 3)
