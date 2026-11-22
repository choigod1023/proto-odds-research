"""Bounded, resumable KBL box capture; no background jobs or production access."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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


class RateLimitError(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__('HTTP 429: batch stopped')


def next_eligible(value, now):
    try:
        seconds = int(value)
        return now + timedelta(seconds=max(seconds,60))
    except (ValueError,TypeError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.utcoffset() is None: raise ValueError('timezone')
            return max(parsed.astimezone(timezone.utc),now+timedelta(seconds=60))
        except (ValueError,TypeError,OverflowError):
            return now+timedelta(seconds=60)


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
            if e.code == 429:
                retry_after = str(e.headers.get('Retry-After',''))[:100] if e.headers else ''
                if attempt == 2 or retry_after:
                    raise RateLimitError(retry_after) from e
            if e.code not in (429,500,502,503,504) or attempt == 2:
                raise
        except (TimeoutError, OSError) as e:
            if attempt == 2:
                raise e
        sleep(3 * 2**attempt)
    raise RuntimeError('unreachable')


@contextmanager
def singleflight(folder):
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'.capture.lock').open('a+b') as f:
        f.seek(0)
        if not f.read(1): f.write(b'0'); f.flush()
        f.seek(0)
        if __import__('os').name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally:
            f.seek(0)
            if __import__('os').name == 'nt': msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=DEFAULT)
    parser.add_argument('--max-new', type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_new <= 526:
        raise ValueError('max-new must be 1..526')
    with singleflight(args.cache):
        run_batch(args.cache,args.max_new)


def run_batch(cache, max_new):
    rate_path=cache/'rate-limit.json'
    if rate_path.exists():
        prior=json.loads(rate_path.read_text(encoding='utf-8'))
        if datetime.now(timezone.utc) < datetime.fromisoformat(prior['next_eligible']):
            raise RateLimitError(prior.get('retry_after',''))
    counts = {'cached':0,'downloaded':0,'failed':0}
    for game in game_ids():
        if not (cache/(game+'.json')).exists() and counts['downloaded']+counts['failed'] >= max_new:
            break
        try:
            status = capture(game,cache)
            counts[status] += 1
            if status == 'downloaded':
                time.sleep(1.05)
        except RateLimitError as e:
            now=datetime.now(timezone.utc)
            record={'game_id':game,'status':429,'at':now.isoformat(),'retry_after':e.retry_after,
                    'next_eligible':next_eligible(e.retry_after,now).isoformat()}
            rate_path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
            raise
        except PermissionError as e:
            with (cache/'failures.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'game_id':game,'error':str(e),'at':datetime.now(timezone.utc).isoformat()})+'\n')
            raise
        except (OSError, ValueError, urllib.error.HTTPError) as e:
            counts['failed'] += 1
            with (cache/'failures.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'game_id':game,'error':type(e).__name__,'at':datetime.now(timezone.utc).isoformat()})+'\n')
            time.sleep(3)
        if sum(counts.values()) % 25 == 0:
            print(json.dumps(counts), flush=True)
    print(json.dumps(counts),flush=True)


if __name__ == '__main__':
    main()
