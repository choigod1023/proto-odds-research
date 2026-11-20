"""Explicit opt-in Sports Mole forecasts, kept separate from verified player facts.

Captures publisher scoreline opinions only, without inventing calibrated probabilities.
No scheduling, production DB writes, model changes or automatic event matching.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

AGENT = 'ProoddResearch/1.0'


def validate_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname != 'www.sportsmole.co.uk'
            or p.port not in (None, 443) or p.username or p.password
            or p.query or p.fragment or '/preview/' not in p.path
            or not p.path.endswith('.html')):
        raise ValueError('unsupported article URL')


def read(session, url, limit):
    with session.get(url, timeout=(5, 15), allow_redirects=False, stream=True,
                     headers={'User-Agent': AGENT}) as response:
        if response.status_code != 200:
            raise ValueError(f'http_{response.status_code}')
        data = bytearray()
        for chunk in response.iter_content(16384):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('response too large')
        return bytes(data), response.headers.get('Content-Type', '')


def parse(raw, url, observed_at):
    validate_url(url)
    observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    if observed.utcoffset() is None or len(raw) > 1_000_000:
        raise ValueError('invalid capture')
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup.select('script,style,nav,footer,aside'):
        tag.decompose()
    title = soup.find('h1')
    picks = []
    for tag in soup.find_all(['h2', 'h3']):
        match = re.fullmatch(r'We say:\s*(.+?)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(.+)',
                             ' '.join(tag.get_text(' ', strip=True).split()), re.I)
        if not match:
            continue
        home, hs, aws, away = match.groups()
        if not title or not all(n.casefold() in title.get_text().casefold() for n in (home, away)):
            continue
        hs, aws = int(hs), int(aws)
        picks.append(dict(home_name=home, away_name=away, predicted_home_score=hs,
                          predicted_away_score=aws,
                          outcome='home' if hs > aws else 'away' if hs < aws else 'draw',
                          probability=None, market='football_90min_1x2'))
    def meta(key):
        tag = soup.find('meta', attrs={'property': key})
        return tag.get('content') if tag else None
    return dict(schema='tipster-opinion-capture-v1', source='sportsmole', source_url=url,
        title=title.get_text(' ', strip=True) if title else '', observed_at=observed_at,
        published_at=meta('article:published_time'), modified_at=meta('article:modified_time'),
        document_sha256=hashlib.sha256(raw).hexdigest(), forecasts=picks,
        status='captured_unlinked' if picks else 'no_supported_prediction',
        official_event_id=None, official_kickoff_at=None, temporal_eligibility='unverified',
        frozen_baseline_probability=None, frozen_context_probability=None,
        official_result=None, contemporaneous_odds=None, incremental_roi_pct=None,
        monthly_loss_limit_krw=50000, affects_production=False,
        blockers=['official fixture mapping and kickoff verification',
                  'same-time odds and frozen baseline/context/challenger predictions',
                  'official result after match; no retrospective probability reconstruction'])


def fetch(url):
    validate_url(url)
    with requests.Session() as session:
        robots_url = 'https://www.sportsmole.co.uk/robots.txt'
        raw, _ = read(session, robots_url, 128000)
        robots = RobotFileParser(robots_url)
        robots.parse(raw.decode('utf-8', errors='replace').splitlines())
        if not robots.can_fetch(AGENT, url):
            raise ValueError('robots disallow')
        raw, mime = read(session, url, 1_000_000)
        if 'text/html' not in mime.lower():
            raise ValueError('not HTML')
        return parse(raw, url, datetime.now(timezone.utc).isoformat())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('snapshots must not be overwritten')
    result = fetch(args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
