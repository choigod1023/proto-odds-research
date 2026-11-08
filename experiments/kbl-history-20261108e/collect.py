"""Bounded, resumable KBL box capture; no background jobs or production access."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT/'experiments/pregame-basketball-20261104/season-records.jsonl'
DEFAULT = Path('C:/Users/user/AppData/Local/Temp/kbl-history-20261108e')
HEADERS = {'User-Agent': 'CodexResearch/1.0 (bounded retrospective research)', 'Channel':'WEB',
           'TeamCode':'XX', 'lang':'ko', 'X-Requested-With':'XMLHttpRequest'}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def game_ids():
    records = [json.loads(x) for x in FIXTURES.read_text(encoding='utf-8').splitlines()]
    fixtures = {r['payload']['source_fixture_id']: r for r in records if r.get('league') == 'KBL' and r.get('kind') == 'fixture'}
    results = {r['payload']['source_fixture_id'] for r in records if r.get('league') == 'KBL' and r.get('kind') == 'result'}
    return sorted((k for k in fixtures if k in results), key=lambda k: (fixtures[k]['kickoff'],k))


def capture(game, folder, opener=urllib.request.urlopen, sleep=time.sleep):
    if not re.fullmatch(r'S\d+G\d+N\d+', game):
        raise ValueError('invalid game id')
    folder.mkdir(parents=True, exist_ok=True)
    meta_path, body_path = folder/(game+'.json'), folder/(game+'.body.gz')
    url = f'https://api.kbl.or.kr/match/{game}/player-stat'
    if meta_path.exists():
        m = json.loads(meta_path.read_text(encoding='utf-8'))
        if m['url'] != url or m['status'] != 200 or not body_path.exists():
            raise ValueError('invalid cached receipt')
        if digest(gzip.decompress(body_path.read_bytes())) != m['sha256']:
            raise ValueError('cache hash mismatch')
        return 'cached'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with opener(req, timeout=15) as response:
                status = response.status
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ValueError('body exceeds cap')
            if status != 200:
                raise urllib.error.HTTPError(url,status,'non-200',{},None)
            rows = json.loads(body)
            if not isinstance(rows, list) or not rows:
                raise ValueError('invalid or empty response')
            receipt = {'game_id': game, 'url':url, 'status':status, 'received_at':datetime.now(timezone.utc).isoformat(),
                       'timestamp_basis':'http_response_body_completed','sha256':digest(body),'bytes':len(body),'rows':len(rows)}
            body_path.write_bytes(gzip.compress(body,mtime=0))
            meta_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            return 'downloaded'
        except urllib.error.HTTPError as e:
            if e.code in (401,403):
                raise PermissionError(f'HTTP {e.code}; stopping') from e
            if e.code not in (429,500,502,503,504) or attempt == 2:
                raise
        except (TimeoutError, OSError) as e:
            if attempt == 2:
                raise e
        sleep(3 * 2**attempt)
    raise RuntimeError('unreachable')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=DEFAULT)
    parser.add_argument('--max-new', type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_new <= 526:
        raise ValueError('max-new must be 1..526')
    counts = {'cached':0,'downloaded':0,'failed':0}
    for game in game_ids():
        if not (args.cache/(game+'.json')).exists() and counts['downloaded']+counts['failed'] >= args.max_new:
            break
        try:
            status = capture(game,args.cache)
            counts[status] += 1
            if status == 'downloaded':
                time.sleep(1.05)
        except PermissionError:
            raise
        except (OSError, ValueError, urllib.error.HTTPError) as e:
            counts['failed'] += 1
            with (args.cache/'failures.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'game_id':game,'error':type(e).__name__,'at':datetime.now(timezone.utc).isoformat()})+'\n')
            time.sleep(3)
        if sum(counts.values()) % 25 == 0:
            print(json.dumps(counts), flush=True)
    print(json.dumps(counts),flush=True)


if __name__ == '__main__':
    main()
