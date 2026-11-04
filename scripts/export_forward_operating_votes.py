"""Read-only bounded Fly SQLite export. No network, writes, or settlement.

--event-id means the *internal* operating ID. --official-id discovery scans
at most 3000 recent prediction records and requires an exact identity/product;
a cap hit or ambiguity refuses export. Counts and rows share one transaction.
"""
import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

MAX_ROWS = 1000
MAX_EVENTS = 100
MAX_BYTES = 32 * 1024 * 1024
MAX_SCAN = 3000
TEAMS = {'HH': '한화', 'KT': 'KT', 'SS': '삼성', 'OB': '두산', 'LG': 'LG',
         'WO': '키움', 'HT': 'KIA', 'LT': '롯데', 'NC': 'NC', 'SK': 'SSG'}


def moment(value):
    x = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if x.utcoffset() is None:
        raise ValueError('timezone_required')
    return x.astimezone(timezone.utc)


def offer_matches(option, fixture, market_id):
    parts = market_id.split(':')
    if len(parts) != 3 or parts[0] != 'G101' or not all(x.isdigit() for x in parts[1:]):
        return False
    if str(fixture.get('round')) != str(int(parts[1][-3:])):
        return False
    for n in (parts[2], int(parts[2])):
        raw = '|'.join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                       for x in (option.get('selection_id'), fixture['round'], n))
        if 'off_' + hashlib.sha256(raw.encode()).hexdigest()[:16] == option.get('offer_id'):
            return True
    return False


def fixture_matches(row, target):
    fixture = row.get('features', {}).get('fixture', {})
    if (fixture.get('sport') != 'bs' or fixture.get('league') != 'KBO'
            or fixture.get('year') != target['season']
            or fixture.get('home') != TEAMS.get(target['home_id'])
            or fixture.get('away') != TEAMS.get(target['away_id'])
            or moment(row['kickoff']) != moment(target['kickoff'])):
        return False
    match = re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})', str(fixture.get('date', '')))
    if not match:
        return False
    local = datetime(target['season'], *map(int, match.groups()), tzinfo=timezone(timedelta(hours=9)))
    if local != moment(target['kickoff']):
        return False
    ids = [fixture.get('source_event_id'), fixture.get('match_id')]
    if any(ids) and target['official_id'] not in ids:
        return False
    options = [o for o in row.get('features', {}).get('options', [])
               if o.get('market') == '승패' and not o.get('label') and o.get('line') is None]
    return (len(options) == 2 and {o.get('selection') for o in options} == {'홈', '원정'}
            and all(offer_matches(o, fixture, target['market_id']) for o in options))


def discover(conn, target, now):
    if (not target.get('official_id') or target.get('home_id') not in TEAMS
            or target.get('away_id') not in TEAMS or target['home_id'] == target['away_id']
            or type(target.get('season')) is not int):
        raise ValueError('discovery_target_invalid')
    cutoff = (now - timedelta(days=3)).isoformat()
    cursor = conn.execute("SELECT record_json FROM prediction_records WHERE record_type='prediction' AND julianday(captured_at)>=julianday(?) ORDER BY ledger_sequence DESC LIMIT ?", (cutoff, MAX_SCAN + 1))
    event_ids = set(); seen = 0; size = 0
    for (raw,) in cursor:
        seen += 1
        if seen > MAX_SCAN:
            raise ValueError('discovery_scan_limit_exceeded')
        size += len(raw.encode('utf-8'))
        if size > MAX_BYTES:
            raise ValueError('discovery_byte_limit_exceeded')
        row = json.loads(raw)
        if fixture_matches(row, target): event_ids.add(row['event_id'])
    if len(event_ids) != 1:
        raise ValueError('discovery_no_match' if not event_ids else 'discovery_ambiguous')
    return next(iter(event_ids)), {'official_id': target['official_id'],
           'internal_event_id': next(iter(event_ids)), 'home_id': target['home_id'],
           'away_id': target['away_id'], 'kickoff': target['kickoff'],
           'market_id': target['market_id'], 'rows_scanned': seen, 'window_days': 3,
           'mapping': 'exact_kbo_fixture_and_both_offer_ids'}


def export(db, events=None, target=None):
    events = list(events or [])
    if target is None and (not events or len(events) > MAX_EVENTS or len(set(events)) != len(events)):
        raise ValueError('event_id_bound_or_duplicates')
    if target is not None and events:
        raise ValueError('choose_internal_ids_or_discovery')
    if any(not isinstance(x, str) or not x or len(x) > 128 for x in events):
        raise ValueError('invalid_event_id')
    uri = 'file:' + quote(str(Path(db).resolve()).replace('\\', '/'), safe='/:') + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=3)
    conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
    try:
        started = datetime.now(timezone.utc); mapping = None
        if target is not None:
            event_id, mapping = discover(conn, target, started)
            events = [event_id]
        counts = {}; rows = []; total_bytes = 0
        for event in events:
            count, max_sequence, byte_count = conn.execute(
                "SELECT count(*),max(ledger_sequence),coalesce(sum(length(cast(record_json AS blob))),0) FROM prediction_records WHERE event_id=? AND record_type='prediction'", (event,)).fetchone()
            counts[event] = {'count': count, 'max_sequence': max_sequence}
            if count > MAX_ROWS or len(rows) + count > MAX_ROWS:
                raise ValueError('export_row_limit_exceeded')
            total_bytes += byte_count
            if total_bytes > MAX_BYTES: raise ValueError('export_byte_limit_exceeded')
            found = conn.execute("SELECT record_json FROM prediction_records WHERE event_id=? AND record_type='prediction' ORDER BY ledger_sequence", (event,)).fetchall()
            if len(found) != count: raise ValueError('count_changed_inside_transaction')
            rows.extend(json.loads(x[0]) for x in found)
        stamp = datetime.now(timezone.utc).isoformat()
        source = globals().get('__bridge_source')
        if source is None: source = Path(__file__).read_bytes()
        return {'metadata': {'schema': 'operating-event-export-v1', 'events': counts,
                             'mapping': mapping, 'queried_at': stamp,
                             'query_mode': 'sqlite_readonly_transaction',
                             'exporter_sha256': hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest(), 'full_chain': False},
                'records': rows, 'evidence': {}}
    finally:
        conn.rollback(); conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True); p.add_argument('--event-id', action='append')
    p.add_argument('--official-id'); p.add_argument('--season', type=int)
    p.add_argument('--home-id'); p.add_argument('--away-id'); p.add_argument('--kickoff'); p.add_argument('--market-id')
    args = p.parse_args()
    target = None
    if args.official_id:
        target = dict(official_id=args.official_id, season=args.season, home_id=args.home_id,
                      away_id=args.away_id, kickoff=args.kickoff, market_id=args.market_id)
    print(json.dumps(export(args.db, args.event_id, target), ensure_ascii=False, separators=(',', ':'), allow_nan=False))


if __name__ == '__main__': main()
