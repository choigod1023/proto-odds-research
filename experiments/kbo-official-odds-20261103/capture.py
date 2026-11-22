"""Bounded, read-only official API probe; records failures without inventing responses."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
BASE = 'https://www.betman.co.kr'
PROBES = [
    (f'detail-{year}-{round_no}', 'POST', '/buyPsblGame/gameInfoInq.do',
     {'gmId': 'G101', 'gmTs': f'{year % 100:02d}{round_no:04d}',
      'gameYear': str(year), '_sbmInfo': {'debugMode': 'false'}})
    for year, round_no in ((2023, 43), (2024, 39), (2026, 40))
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def probe(session, name, method, path, payload):
    record = {'name': name, 'method': method, 'url': BASE + path,
              'request_json': payload, 'timeout_seconds': 15}
    try:
        response = session.request(method, BASE + path, json=payload, timeout=15,
                                   headers={'User-Agent': 'Mozilla/5.0',
                                            'Referer': BASE + '/main/mainPage/gamebuy/closedGameList.do',
                                            'X-Requested-With': 'XMLHttpRequest'})
        record.update(received_at=datetime.now(timezone.utc).isoformat(),
                      http_status=response.status_code,
                      content_type=response.headers.get('Content-Type'),
                      body_sha256=sha256(response.content),
                      body_file=f'evidence/{name}.body')
        (ROOT / record['body_file']).write_bytes(response.content)
    except requests.RequestException as exc:
        record.update(failed_at=datetime.now(timezone.utc).isoformat(),
                      http_status=None, body_sha256=None, body_file=None,
                      error_type=type(exc).__name__, error=str(exc))
    return record


def main():
    (ROOT / 'evidence').mkdir(exist_ok=True)
    attempts_path = ROOT / 'evidence' / 'attempts.json'
    attempts = json.loads(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else []
    if any(item.get('http_status') in (403, 429) for item in attempts):
        raise RuntimeError('Previous 403/429 recorded; do not automatically retry')
    with requests.Session() as session:
        for args in PROBES:
            if any(item['name'] == args[0] and item.get('http_status') == 200 for item in attempts):
                continue
            result = probe(session, *args)
            attempts.append(result)
            if result.get('http_status') in (403, 429):
                break
            time.sleep(1)
    attempts_path.write_text(
        json.dumps(attempts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(attempts, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
