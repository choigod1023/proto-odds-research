"""Bounded Betman G101 snapshot collector. Historical snapshots are not historical quotes."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = 'https://www.betman.co.kr'
DETAIL = BASE + '/buyPsblGame/gameInfoInq.do'
CLOSED = BASE + '/buyPsblGame/closedList.do'
EVIDENCE = Path('C:/Users/user/Documents/Codex/pregame-proto-price-evidence')
MAX_BODY_BYTES = 16 * 1024 * 1024
OFFLINE_ROOT = Path(__file__).resolve().parents[1] / 'experiments/pregame-proto-prices-20261104'
KST = timezone(timedelta(hours=9))
OLD = [Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103'), Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103')]


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def body_from_cache(root, meta):
    raw = gzip.decompress((root / meta['body_file']).read_bytes())
    if digest(raw) != meta['body_sha256']:
        raise ValueError('cache SHA-256 mismatch')
    return raw


def old_detail(ts):
    for root in OLD:
        ledger = root / 'evidence/attempts.json'
        if not ledger.exists():
            continue
        for entry in json.loads(ledger.read_text(encoding='utf-8')):
            payload = entry.get('request_json') or {}
            if entry.get('url') != DETAIL or str(payload.get('gmTs')) != ts or entry.get('http_status') != 200:
                continue
            path = root / entry['body_file']
            if not path.is_file():
                continue
            raw = path.read_bytes()
            raw = gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
            if digest(raw) != entry['body_sha256']:
                raise ValueError('old cache SHA-256 mismatch')
            # Old capture code cannot prove completion-time ordering: never upgrade it.
            return raw, dict(entry, timestamp_basis='legacy_unverified', legacy_cache=True)
    return None


def normalize(data, meta, start, end, *, modern_soccer_leagues=False):
    ts = str(data['gmTs']).zfill(6)
    lottery = data.get('currentLottery') or {}
    if str(lottery.get('gmId')) != 'G101' or str(lottery.get('gmTs')).zfill(6) != ts:
        raise ValueError('unexpected round identity')
    c = data['compSchedules']
    if len(c['keys']) != len(set(c['keys'])):
        raise ValueError('duplicate response keys')
    result = []
    received = datetime.fromisoformat(meta['received_at'])
    for values in c['datas']:
        if len(values) != len(c['keys']):
            raise ValueError('malformed schedule')
        row = dict(zip(c['keys'], values))
        sport = str(row.get('itemCode') or '').lower()
        if sport not in ('sc', 'bs', 'bk'):
            continue
        legacy = row.get('betId') is None and row.get('betTypId') is None
        modern = ((sport == 'sc' and row.get('betId') == '1' and row.get('betTypId') == '1' and row.get('betNm') == '축구 승무패') or
                  (sport == 'bs' and row.get('betId') == '2' and row.get('betTypId') == '2'))
        # Modern G101 soccer includes numeric/alphanumeric league codes. Opt in
        # only with explicit soccer product IDs and official league short name.
        league = row.get('leagueCode')
        if not str(league or '').startswith(sport.upper()) and not (
            modern_soccer_leagues and sport == 'sc' and modern
            and isinstance(league, str) and league.strip()
            and isinstance(row.get('leagueShortName'), str) and row['leagueShortName'].strip()
        ):
            continue
        # Basketball modern market IDs are not established by the reused evidence.
        if not (legacy or modern) or (legacy and row.get('handi') != 0) or (sport == 'sc' and row.get('handi') != 0):
            continue
        if row.get('gameSubject') not in (None, '') or any(row.get(k) not in (None, 0, 0.0) for k in ('winHandi', 'drawHandi', 'loseHandi')):
            continue
        if row.get('gameReject') != '0' or row.get('buyReject') != '0':
            continue
        try:
            kickoff = datetime.fromtimestamp(row['gameDate'] / 1000, KST)
            odds = {'home': float(row['winAllot']), 'away': float(row['loseAllot'])}
            draw = float(row.get('drawAllot') or 0)
            if sport == 'sc':
                odds['draw'] = draw
            elif draw != 0:
                continue
            if not all(math.isfinite(p) and 1 < p < 1000 for p in odds.values()):
                continue
            if not start <= kickoff.date() <= end or not row.get('homeName') or not row.get('awayName') or row.get('matchSeq') is None:
                continue
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            continue
        # Actual receipt requires verified body completion, open round, sale window and future match.
        try:
            opened = (lottery.get('saleProgress') is True and lottery['saleStartDate'] <= received.timestamp()*1000 < lottery['saleEndDate'] and received < kickoff and not meta.get('legacy_cache') and meta.get('timestamp_basis') == 'http_response_body_completed')
        except (KeyError, TypeError):
            opened = False
        product = f"G101:{ts}:{row['matchSeq']}"
        record_id = digest((product + ':' + meta['body_sha256'] + ':' + meta['received_at']).encode())
        result.append({'record_id': record_id, 'kind': 'price', 'sport': sport, 'league': row['leagueCode'], 'kickoff': kickoff.isoformat(), 'home': row['homeName'], 'away': row['awayName'], 'source_url': meta['url'], 'body_sha256': meta['body_sha256'], 'received_at': meta['received_at'], 'timing_basis': 'actual_receipt' if opened else 'retrospective', 'timestamp_basis': meta['timestamp_basis'], 'payload': {'product_id': product, 'period': 'full', 'market': 'winner_3way' if sport == 'sc' else 'winner_2way', 'line': None, 'odds': odds, 'settlement_rule': 'unknown', 'source_status': {'saleProgress': lottery.get('saleProgress'), 'saleStatus': lottery.get('saleStatus'), 'protoStatus': row.get('protoStatus'), 'gameReject': row.get('gameReject'), 'buyReject': row.get('buyReject')}, 'execution_price_verified': False}})
    ids = [r['payload']['product_id'] for r in result]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate product IDs')
    return result




def normalize_v2(data, meta, start, end, *, modern_soccer_leagues=False):
    """Enrich v1 without altering product, price, timing, or team name fields."""
    records = normalize(data, meta, start, end, modern_soccer_leagues=modern_soccer_leagues)
    schedules = data['compSchedules']
    by_sequence = {}
    for values in schedules['datas']:
        row = dict(zip(schedules['keys'], values))
        seq = row.get('matchSeq')
        if seq in by_sequence:
            raise ValueError('duplicate source match sequence')
        by_sequence[seq] = row
    fields = ('leagueName', 'leagueCode', 'homeId', 'awayId', 'homeName', 'awayName',
              'gameDate', 'betId', 'betTypId', 'betNm')
    for record in records:
        seq = record['payload']['product_id'].rsplit(':', 1)[1]
        matches = [row for key, row in by_sequence.items() if str(key) == seq]
        if len(matches) != 1:
            raise ValueError('ambiguous source identity: ' + seq)
        row = matches[0]
        identity = {field: row.get(field) for field in fields}
        if modern_soccer_leagues:
            identity['leagueShortName'] = row.get('leagueShortName')
        league_name = row.get('leagueName')
        record['league'] = league_name if isinstance(league_name, str) and league_name.strip() else row['leagueCode']
        identity['league_basis'] = 'leagueName' if isinstance(league_name, str) and league_name.strip() else 'leagueCode_fallback'
        record['payload']['source_identity'] = identity
    return records


def offline_cache(manifest_path, output, schema="v1", v2_manifest_path=None):
    """Rebuild only from the frozen, hash-pinned legacy response list; never use HTTP."""
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    start, end = date.fromisoformat(manifest['start']), date.fromisoformat(manifest['end'])
    records = []
    enriched = []
    seen = set()
    for source in manifest['sources']:
        ts = source['round']
        if ts in seen or not re.fullmatch(r'\d{6}', ts):
            raise ValueError('duplicate or invalid offline round')
        seen.add(ts)
        root = OLD[source['source_root_index']]
        path = (root / source['body_file']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('legacy source outside expected root')
        raw = path.read_bytes()
        raw = gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw
        if digest(raw) != source['body_sha256']:
            raise ValueError('offline source SHA-256 mismatch: ' + ts)
        data = json.loads(raw)
        if str(data.get('gmTs')).zfill(6) != ts:
            raise ValueError('offline round mismatch: ' + ts)
        meta = {'url': source['source_url'], 'body_sha256': source['body_sha256'],
                'received_at': source['received_at'], 'timestamp_basis': 'legacy_unverified', 'legacy_cache': True}
        base = normalize(data, meta, start, end)
        records.extend(base)
        if schema == 'v2':
            v2 = normalize_v2(data, meta, start, end)
            if len(v2) != len(base) or any(a['record_id'] != b['record_id'] or a['payload']['odds'] != b['payload']['odds'] for a, b in zip(base, v2)):
                raise ValueError('v2 prices differ from v1')
            enriched.extend(v2)
    raw_output = ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in records).encode('utf-8')
    if len(records) != manifest['record_count'] or digest(raw_output) != manifest['normalized_sha256']:
        raise ValueError('offline normalized output does not match frozen manifest')
    if schema == 'v2':
        v2_manifest = json.loads(Path(v2_manifest_path).read_text(encoding='utf-8'))
        if v2_manifest['sources'] != manifest['sources'] or v2_manifest['v1_normalized_sha256'] != manifest['normalized_sha256']:
            raise ValueError('v2 sources or v1 reference do not match frozen manifest')
        raw_output = ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in enriched).encode('utf-8')
        if len(enriched) != v2_manifest['record_count'] or digest(raw_output) != v2_manifest['normalized_sha256']:
            raise ValueError('v2 normalized output does not match frozen manifest')
        records = enriched
    elif schema != 'v1':
        raise ValueError('invalid schema')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix == '.gz':
        output.write_bytes(gzip.compress(raw_output, mtime=0))
    else:
        output.write_bytes(raw_output)
    return {'status': 'completed', 'source_round_count': len(seen), 'record_count': len(records),
            'sports': dict(Counter(r['sport'] for r in records)), 'normalized_sha256': digest(raw_output), 'output': str(output)}


def write_selected_output(cap, state, output):
    chunks = []
    for ts in state['rounds'][:state['next_round_cursor']]:
        chunks.append((cap / ('records-' + ts + '.jsonl')).read_bytes())
    raw = b''.join(chunks)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(raw, mtime=0) if output.suffix == '.gz' else raw)


def run(args):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.capture_id):
        raise ValueError('invalid capture id')
    if not 1 <= args.max_requests <= 20 or args.total_timeout <= 0:
        raise ValueError('request cap must be 1..20 and timeout positive')
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end:
        raise ValueError('invalid date range')
    evidence = Path(getattr(args, 'evidence', None) or EVIDENCE)
    evidence.mkdir(parents=True, exist_ok=True)
    # A restriction remains in force across capture IDs; never bypass by changing labels.
    if (EVIDENCE / 'STOP_403_429.json').exists() or (evidence / 'STOP_403_429.json').exists():
        raise RuntimeError('previous 403/429: stop; no automatic retry')
    def stop_all(meta):
        # A different --evidence path must not bypass a previous 403/429.
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        write_json(EVIDENCE / 'STOP_403_429.json', meta)
        if evidence.resolve() != EVIDENCE.resolve():
            write_json(evidence / 'STOP_403_429.json', meta)
    cap = evidence / args.capture_id
    cap.mkdir(exist_ok=True)
    state_path = cap / 'state.json'
    config = {'start': args.start, 'end': args.end, 'round': args.round, 'page_size': args.page_size}
    if getattr(args, 'schema', 'v1') == 'v2':
        config['schema'] = 'v2'
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state['config'] != config:
            raise ValueError('capture ID already used with different parameters')
    else:
        state = {'config': config, 'page_cursor': 0, 'rounds': [], 'next_round_cursor': 0, 'listing_done': bool(args.round), 'events': []}
        if args.round:
            state['rounds'] = [args.round]
    def event(status, **kw):
        state['events'].append({'status': status, 'at': now(), **kw})
        write_json(state_path, state)
    count = 0
    deadline = time.monotonic() + args.total_timeout
    def fetch(url, payload, key):
        nonlocal count
        mp = cap / (key + '.json')
        if mp.exists():
            meta = json.loads(mp.read_text(encoding='utf-8'))
            raw = body_from_cache(cap, meta)
            if meta['http_status'] != 200:
                raise RuntimeError('cached HTTP failure; no automatic retry')
            return raw, meta
        if count >= args.max_requests or time.monotonic() >= deadline:
            raise StopIteration('request budget or total timeout')
        count += 1
        # Streaming and a monotonic deadline bound body reads as well as connection time.
        remaining = deadline - time.monotonic()
        with requests.post(url, json=payload, stream=True, timeout=(min(10, remaining), min(5, remaining)), headers={'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest', 'Referer': BASE + '/main/mainPage/gamebuy/closedGameList.do'}, allow_redirects=False) as response:
            if response.status_code in (403, 429):
                stop_all({'url': url, 'http_status': response.status_code, 'headers_received_at': now()})
            parts = []
            size = 0
            for chunk in response.iter_content(65536):
                if time.monotonic() >= deadline:
                    raise TimeoutError('total timeout during body read')
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    raise ValueError('response body exceeds 16 MiB limit')
                parts.append(chunk)
            raw = b''.join(parts)
            received = now()
            meta = {'url': url, 'request_json': payload, 'received_at': received, 'timestamp_basis': 'http_response_body_completed', 'http_status': response.status_code, 'body_sha256': digest(raw), 'body_file': key + '.body.gz'}
            (cap / meta['body_file']).write_bytes(gzip.compress(raw, mtime=0))
            write_json(mp, meta)
            if response.status_code in (403, 429):
                stop_all(meta)
                raise PermissionError(f'HTTP {response.status_code}')
            if response.status_code != 200:
                raise RuntimeError(f'HTTP {response.status_code}')
            return raw, meta
    event('started')
    try:
        while not state['listing_done']:
            page = state['page_cursor']
            raw, _ = fetch(CLOSED, {'gmId': 'G101', 'draw': 1, 'start': page, 'length': args.page_size, '_sbmInfo': {'debugMode': 'false'}}, f'page-{page}')
            data = json.loads(raw)['schedules']
            rows = data['data']
            for r in rows:
                if r.get('gmOsidTsYear') in range(start.year, end.year + 1):
                    ts = str(r['gmTs']).zfill(6)
                    if ts not in state['rounds']:
                        state['rounds'].append(ts)
            state['page_cursor'] += len(rows)
            state['listing_done'] = not rows or state['page_cursor'] >= data['recordsFiltered'] or min((r.get('gmOsidTsYear', 9999) for r in rows), default=9999) < start.year
            write_json(state_path, state)
        while state['next_round_cursor'] < len(state['rounds']):
            index = state['next_round_cursor']
            ts = state['rounds'][index]
            if not re.fullmatch(r'\d{6}', ts):
                raise ValueError('invalid round')
            # Reuse old snapshots only for already closed historical rounds.
            old = old_detail(ts) if not args.round else None
            if old:
                raw, meta = old
            else:
                raw, meta = fetch(DETAIL, {'gmId': 'G101', 'gmTs': ts, 'gameYear': '20' + ts[:2], '_sbmInfo': {'debugMode': 'false'}}, 'round-' + ts)
            data = json.loads(raw)
            if str(data.get('gmTs')).zfill(6) != ts:
                raise ValueError('wrong round returned')
            records = (normalize_v2 if getattr(args, 'schema', 'v1') == 'v2' else normalize)(data, meta, start, end)
            out = cap / ('records-' + ts + '.jsonl')
            out.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
            state['next_round_cursor'] += 1
            write_json(state_path, state)
        event('completed', requests=count)
    except StopIteration as exc:
        event('interrupted', reason=str(exc), requests=count)
    except (Exception, KeyboardInterrupt) as exc:
        event('interrupted' if isinstance(exc, (PermissionError, KeyboardInterrupt)) else 'failed', reason=str(exc), requests=count)
        if not isinstance(exc, KeyboardInterrupt):
            raise
    if getattr(args, 'output', None):
        write_selected_output(cap, state, args.output)
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture-id', help='required for online collection; use a new ID for a fresh capture')
    p.add_argument('--offline-cache', action='store_true', help='rebuild from the frozen historical manifest without network access')
    p.add_argument('--schema', choices=('v1', 'v2'), default='v1', help='v2 preserves official source identity fields')
    p.add_argument('--evidence', type=Path, default=EVIDENCE, help='capture evidence directory')
    p.add_argument('--output', type=Path, help='combined JSONL output (gzip when the filename ends in .gz)')
    p.add_argument('--start', default='2025-01-01')
    p.add_argument('--end', default='2026-11-04')
    p.add_argument('--round', help='explicit six-digit G101 round; fetches a fresh snapshot')
    p.add_argument('--page-size', type=int, choices=range(1, 21), default=20)
    p.add_argument('--max-requests', type=int, default=20)
    p.add_argument('--total-timeout', type=float, default=120)
    args = p.parse_args()
    if args.offline_cache:
        if args.capture_id or args.round:
            p.error('--offline-cache cannot be combined with --capture-id or --round')
        default_name = 'normalized-price-records-v2.jsonl.gz' if args.schema == 'v2' else 'normalized-price-records.jsonl.gz'
        output = args.output or OFFLINE_ROOT / default_name
        frozen_v1 = {OFFLINE_ROOT / 'manifest.json', OFFLINE_ROOT / 'normalized-price-records.jsonl.gz'}
        if args.schema == 'v2' and Path(output).resolve() in {p.resolve() for p in frozen_v1}:
            p.error('v2 output cannot overwrite frozen v1 artifacts')
        result = offline_cache(OFFLINE_ROOT / 'manifest.json', output, args.schema, OFFLINE_ROOT / 'manifest-v2.json')
    else:
        if not args.capture_id:
            p.error('--capture-id is required for online collection')
        result = run(args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
