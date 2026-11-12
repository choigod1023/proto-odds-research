import pytest
from src.public_pregame_sources import extract_article, link_claims, source_for, fetch_article, MAX_BYTES

URL = 'https://www.sportsmole.co.uk/football/preview/a-vs-b.html'
NOW = '2026-10-25T08:00:00+00:00'
HTML = b'''<h1>Alpha vs Beta - prediction, team news, lineups</h1>
<meta property="article:published_time" content="2026-10-25T07:00:00Z">
<h2>Match preview</h2><p>Alpha are resting everyone.</p>
<h2>Team News</h2><p>Smith is doubtful with an injury.</p>
<p>Jones is not injured.</p><p>There is a minutes restriction for Lee.</p>
<p>Alpha possible starting lineup: Lee; Smith; Jones.</p>
<p>Bet on Alpha because Beta have injuries.</p>
<h2>We say: Alpha 2-0 Beta</h2><p>With Smith injured, Alpha will win.</p>'''


def fixture():
    return dict(event_id='e1', sport='sc', league='EPL', home='Alpha', away='Beta',
                kickoff_at='2026-10-25T10:00:00Z', prediction_at='2026-10-25T08:30:00Z',
                mapping_reviewed=True, source_team_names=['Alpha', 'Beta'])


def test_only_news_not_prediction_or_general_preview():
    doc = extract_article(HTML, URL, NOW)
    assert len(doc['claims']) == 4
    assert all('will win' not in c['summary'] and 'Bet on' not in c['summary'] for c in doc['claims'])
    assert any(c['summary'] == 'Jones is not injured.' for c in doc['claims'])
    assert all(c['verification']['confirmed'] is False for c in doc['claims'])
    assert not doc['training_eligible']


def test_does_not_read_prediction_as_fallback():
    doc = extract_article(b'<h2>Prediction</h2><p>Smith injured; Alpha wins.</p>', URL, NOW)
    assert doc['status'] == 'no_supported_claims'


@pytest.mark.parametrize('url', ['http://www.sportsmole.co.uk/a',
    'https://www.sportsmole.co.uk.evil.test/a', 'https://127.0.0.1/a',
    'https://user:pass@www.sportsmole.co.uk/a', 'https://www.sportsmole.co.uk:444/a',
    'https://www.sportsmole.co.uk/a?nocache=1'])
def test_url_allowlist(url):
    with pytest.raises(ValueError):
        source_for(url)


def test_unsupported_source_is_not_silently_scraped():
    with pytest.raises(ValueError, match='manual_review_only'):
        fetch_article('https://www.olbg.com/betting-tips/')


def test_matching_is_explicit_and_still_unverified():
    doc = extract_article(HTML, URL, NOW)
    rows, status = link_claims(doc, fixture())
    assert status == 'eligible'
    assert len(rows) == 4 and all(not r['usable_for_model'] for r in rows)
    f = fixture(); f['mapping_reviewed'] = False
    assert link_claims(doc, f)[1] == 'missing_reviewed_fixture'
    f = fixture(); f['source_team_names'] = ['Al', 'Beta']
    assert link_claims(doc, f)[1] == 'source_teams_not_in_title'


@pytest.mark.parametrize('observed,reason', [
    ('2026-10-25T09:00:00Z', 'observed_after_prediction'),
    ('2026-10-26T08:00:00Z', 'observed_after_prediction')])
def test_later_fetch_is_not_backdated(observed, reason):
    assert link_claims(extract_article(HTML, URL, observed), fixture())[1] == reason


def test_t30_and_publication_guards():
    doc = extract_article(HTML, URL, NOW)
    f = fixture(); f['prediction_at'] = '2026-10-25T09:31:00Z'
    assert link_claims(doc, f)[1] == 'after_t30'
    for value in [None, '2026-10-25T09:00:00Z', '2026-10-20T07:00:00Z']:
        doc['published_at'] = value
        assert link_claims(doc, fixture())[1] == 'missing_future_or_stale_publication'


def test_size_and_aware_time():
    with pytest.raises(ValueError):
        extract_article(b'x' * (MAX_BYTES+1), URL, NOW)
    with pytest.raises(ValueError):
        extract_article(HTML, URL, '2026-10-25T08:00:00')


class Response:
    def __init__(self, body, status=200, content_type='text/html'):
        self.body, self.status_code = body, status
        self.headers = {'Content-Type': content_type}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield self.body


class Session:
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs)); return next(self.responses)


def test_robots_denial_and_failure_stop_before_article():
    for response in [Response(b'User-agent: *\nDisallow: /'), Response(b'', 403)]:
        session = Session([response])
        with pytest.raises(ValueError): fetch_article(URL, session)
        assert len(session.calls) == 1


def test_fetch_no_redirect_and_preserve_real_observation():
    session = Session([Response(b'User-agent: *\nAllow: /'), Response(HTML)])
    doc = fetch_article(URL, session)
    assert doc['observed_at'] != doc['published_at']
    assert all(c[1]['allow_redirects'] is False for c in session.calls)
    session = Session([Response(b'User-agent: *\nAllow: /'), Response(b'', 302)])
    with pytest.raises(ValueError, match='http_302'): fetch_article(URL, session)


def test_duplicate_paragraph_is_only_one_claim():
    raw = b'<h2>Team News</h2><p>Smith injured.</p><p>Smith injured.</p>'
    assert len(extract_article(raw, URL, NOW)['claims']) == 1


def test_lineup_label_requires_names_and_never_crosses_prediction_section():
    raw = b'<h2>Team News</h2><p>Alpha possible starting lineup:</p><p>Smith; Lee; Jones</p>'
    claims = extract_article(raw, URL, NOW)['claims']
    assert len(claims) == 1 and 'Smith; Lee; Jones' in claims[0]['summary']
    raw = b'<h2>Team News</h2><p>Alpha possible starting lineup:</p><h2>We say</h2><p>Alpha wins.</p>'
    assert not extract_article(raw, URL, NOW)['claims']
