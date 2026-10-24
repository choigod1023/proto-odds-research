"""Bounded, read-only public archive probes; save metadata, never article bodies."""
import concurrent.futures
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parent
NBA = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-19_12PM.pdf'
EARLY = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-18_05PM.pdf'
MLB = 'https://www.mlb.com/news/mlb-tokyo-series-2025-game-2-faq-dodgers-vs-cubs'
JOBS = [
    ('early_available', 'https://archive.org/wayback/available', {'url': EARLY, 'timestamp': '20250419165959'}),
    ('early_cdx_before_start', 'https://web.archive.org/cdx/search/cdx', {
        'url': EARLY, 'output': 'json', 'to': '20250419165959',
        'filter': 'statuscode:200', 'fl': 'timestamp,original,digest,statuscode', 'limit': '5'}),
    ('early_origin', EARLY, {}),
    ('early_archive', 'https://web.archive.org/web/20250418223959id_/' + EARLY, {}),
    ('nba_available', 'https://archive.org/wayback/available', {'url': NBA, 'timestamp': '20250419165959'}),
    ('mlb_available', 'https://archive.org/wayback/available', {'url': MLB, 'timestamp': '20250319100959'}),
    ('nba_cdx_before_start', 'https://web.archive.org/cdx/search/cdx', {
        'url': NBA, 'output': 'json', 'to': '20250419165959',
        'filter': 'statuscode:200', 'fl': 'timestamp,original,digest,statuscode', 'limit': '5'}),
    ('nba_origin', NBA, {}),
    ('mlb_origin', MLB, {}),
]


def fetch(job):
    name, url, params = job
    result = {'name': name, 'url': url, 'params': params,
              'request_started_at': datetime.now(timezone.utc).isoformat()}
    try:
        r = requests.get(url, params=params, timeout=20)
        result.update(status=r.status_code, final_url=r.url,
                      observed_at=datetime.now(timezone.utc).isoformat(),
                      content_type=r.headers.get('content-type'),
                      sha256=hashlib.sha256(r.content).hexdigest(),
                      sha1_base32=base64.b32encode(hashlib.sha1(r.content).digest()).decode(),
                      last_modified=r.headers.get('last-modified'), etag=r.headers.get('etag'))
        if name.endswith('available') or name.endswith('start'):
            try:
                result['archive_response'] = r.json()
            except ValueError:
                result['archive_response'] = None
                result['note'] = 'non_json_response_not_saved'
        elif name == 'mlb_origin':
            result['date_metadata'] = re.findall(r'"(date(?:Published|Modified))"\s*:\s*"([^"]+)"', r.text)
        if r.status_code == 200 and r.content.startswith(b'%PDF'):
            import fitz
            with fitz.open(stream=r.content, filetype='pdf') as doc:
                result['pdf_header'] = doc[0].get_text().splitlines()[0]
                content = '\n'.join(page.get_text() for page in doc)
                result['contains_lillard_out'] = bool(re.search(r'Lillard, Damian\s+Out', content))
                result['contains_sheppard_probable'] = bool(re.search(r'Sheppard, Ben\s+Probable', content))
                result['contains_mil_ind'] = 'MIL@IND' in content
                result['contains_ivey_out'] = bool(re.search(r'Ivey, Jaden\s+Out', content))
                result['contains_det_nyk'] = 'DET@NYK' in content
                result['pdf_metadata'] = {k: doc.metadata.get(k) for k in ('creationDate', 'modDate')}
        # HTTP metadata and PDF byte hashes obtained today do not prove old existence.
    except requests.RequestException as exc:
        result.update(observed_at=datetime.now(timezone.utc).isoformat(),
                      error_type=type(exc).__name__, error=str(exc)[:400])
    return result


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        report = list(pool.map(fetch, JOBS))
    (ROOT / 'network_probes.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))
