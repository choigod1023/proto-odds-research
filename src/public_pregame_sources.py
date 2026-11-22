"""Bounded, opt-in public news capture. Never writes production predictions.

Extracted sentences are publisher claims, NOT confirmed player facts. Only an
explicit reviewed fixture mapping may attach an event; no fuzzy team matching.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

try:
    from .pregame_evidence_gate import check_evidence
except ImportError:
    from pregame_evidence_gate import check_evidence

USER_AGENT = 'ProoddResearch/1.0'
MAX_BYTES = 1_000_000
SOURCES = {
    'www.sportsmole.co.uk': {'name': 'sportsmole', 'fetch': True},
    'www.sportsgambler.com': {'name': 'sportsgambler', 'fetch': True},
    'www.wisetoto.com': {'name': 'wisetoto', 'fetch': False},
    'www.olbg.com': {'name': 'olbg', 'fetch': False},
    'www.livescore.co.kr': {'name': 'livescore', 'fetch': False},
}
KINDS = {
    'absence': r'\b(injur\w*|suspend\w*|absen\w*|ruled out|unavailable|doubt\w*)\b',
    'rotation': r'\b(rotat\w*|rested|resting)\b',
    'minutes_limit': r'\b(minutes restriction|minutes limit|limited minutes)\b',
    'rest': r'\b(back-to-back|days? rest|days? of rest)\b',
    'starter': r'\b(starting lineup|starting line-up|starting pitcher|possible starting|expected to start|probable pitcher)\b',
}
PICK = re.compile(r'\b(we say|prediction|betting|bet|bets|odds|pick|picks|tip|tips|over/under)\b', re.I)
SECTION = re.compile(r'^(team news|injuries(?: and suspensions)?|injury report|starting pitchers|probable pitchers|predicted lineups|possible starting lineups|lineups)$', re.I)


def aware(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def source_for(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443)
            or p.hostname not in SOURCES or p.query or p.fragment):
        raise ValueError('unsupported_source_url')
    return SOURCES[p.hostname]


def _read(session, url, limit=MAX_BYTES):
    # No automatic redirects: never fetch unvalidated hosts or bypass a block.
    with session.get(url, timeout=(5, 10), stream=True, allow_redirects=False,
                     headers={'User-Agent': USER_AGENT}) as response:
        if response.status_code != 200:
            raise ValueError(f'http_{response.status_code}')
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > limit:
                raise ValueError('response_too_large')
            chunks.append(chunk)
        return b''.join(chunks), response.headers.get('Content-Type', '')


def fetch_article(url, session=None):
    source = source_for(url)
    if not source['fetch']:
        raise ValueError('manual_review_only')
    owned = session is None
    session = session or requests.Session()
    try:
        parts = urlsplit(url)
        robots_url = f'https://{parts.netloc}/robots.txt'
        raw, _ = _read(session, robots_url, 128_000)
        robots = RobotFileParser(robots_url)
        robots.parse(raw.decode('utf-8', errors='replace').splitlines())
        if not robots.can_fetch(USER_AGENT, url):
            raise ValueError('robots_disallow')
        raw, content_type = _read(session, url)
        if 'text/html' not in content_type.lower():
            raise ValueError('not_html')
        # Timestamp AFTER complete retrieval; publication time cannot backdate it.
        return extract_article(raw, url, datetime.now(timezone.utc).isoformat())
    finally:
        if owned:
            session.close()


def extract_article(raw, url, observed_at):
    source = source_for(url)
    if not source['fetch']:
        raise ValueError('no_reviewed_adapter')
    if len(raw) > MAX_BYTES or aware(observed_at) is None:
        raise ValueError('invalid_capture')
    soup = BeautifulSoup(raw, 'html.parser')
    title = soup.find('h1')
    title = title.get_text(' ', strip=True) if title else ''
    def meta(key):
        tag = soup.find('meta', attrs={'property': key})
        return tag.get('content') if tag else None
    published, modified = meta('article:published_time'), meta('article:modified_time')
    for tag in soup.select('script,style,nav,footer,aside,form'):
        tag.decompose()
    # Strict sections only. The analysis/prediction section is never a fallback.
    active, snippets, seen = False, [], set()
    for tag in soup.find_all(['h2', 'h3', 'h4', 'p']):
        value = ' '.join(tag.get_text(' ', strip=True).split())
        if tag.name != 'p':
            active = bool(SECTION.fullmatch(value))
            continue
        if not active or not value or PICK.search(value):
            continue
        if re.search(r'possible starting lineups?:$', value, re.I):
            # Some publishers put the label and names in separate paragraphs.
            # Never store a bare label as evidence of named starters.
            following = tag.find_next_sibling()
            if following is None or following.name != 'p':
                continue
            names = ' '.join(following.get_text(' ', strip=True).split())
            if not names or PICK.search(names) or ':' in names:
                continue
            value += ' ' + names
        for sentence in re.split(r'(?<=[.!?])\s+', value):
            if len(sentence) > 1000:
                continue
            kinds = [kind for kind, pattern in KINDS.items() if re.search(pattern, sentence, re.I)]
            if not kinds:
                continue
            digest = hashlib.sha256(sentence.casefold().encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            snippets.append({'summary': sentence, 'kinds': kinds, 'content_sha256': digest,
                             'claim_status': 'publisher_claim_requires_review',
                             'verification': {'confirmed': False}})
            if len(snippets) == 30:
                break
        if len(snippets) == 30:
            break
    return {'schema': 'public-pregame-claims-v1', 'source': source['name'],
            'source_url': url, 'title': title, 'observed_at': observed_at,
            'published_at': published, 'modified_at': modified,
            'document_sha256': hashlib.sha256(raw).hexdigest(),
            'status': 'captured' if snippets else 'no_supported_claims',
            'claims': snippets, 'affects_probability': False, 'training_eligible': False}


def link_claims(document, fixture):
    """Adapt to #349 claim audit fields, with a reviewed exact fixture mapping.

    This is only a candidate attachment. It cannot confer training eligibility,
    manufacture official confirmation, or identify a player from prose.
    """
    required = ('event_id', 'sport', 'league', 'home', 'away', 'kickoff_at', 'prediction_at')
    if not all(fixture.get(k) for k in required) or fixture.get('mapping_reviewed') is not True:
        return [], 'missing_reviewed_fixture'
    names = fixture.get('source_team_names')
    if (not isinstance(names, list) or len(names) != 2 or
            any(not isinstance(n, str) or len(n.strip()) < 2 for n in names) or
            names[0].casefold() == names[1].casefold() or
            any(not re.search(r'(?<!\w)' + re.escape(n) + r'(?!\w)', document['title'], re.I) for n in names)):
        return [], 'source_teams_not_in_title'
    prediction, kickoff = aware(fixture['prediction_at']), aware(fixture['kickoff_at'])
    status = check_evidence({**document, 'event_id': fixture['event_id']}, fixture['event_id'],
                            fixture['prediction_at'], fixture['kickoff_at'])
    if status == 'eligible' and prediction > kickoff-timedelta(minutes=30):
        status = 'after_t30'
    # Future or stale publisher metadata must not silently become current facts.
    observed = aware(document['observed_at'])
    updated = aware(document.get('modified_at') or document.get('published_at'))
    if status == 'eligible' and (updated is None or updated > observed or observed-updated > timedelta(hours=48)):
        status = 'missing_future_or_stale_publication'
    records = []
    for claim in document['claims']:
        for kind in claim['kinds']:
            record = {k: fixture[k] for k in required}
            record.update({k: document[k] for k in ('source', 'source_url', 'observed_at', 'published_at', 'modified_at', 'document_sha256')})
            record.update(claim, kind=kind, temporal_status=status,
                          usable_for_model=False, affects_probability=False,
                          verification={'confirmed': False})
            record['id'] = hashlib.sha256((fixture['event_id']+document['source_url']+
                                           document['observed_at']+kind+claim['content_sha256']).encode()).hexdigest()
            records.append(record)
    return records, status
