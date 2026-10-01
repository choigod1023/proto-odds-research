"""Bounded anonymous official MLB retrospective acquisition; never a PIT replay."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://statsapi.mlb.com/api/v1/'
MAX_REQUESTS = 50
MAX_BYTES = 100_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filehash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Store:
    """Append-only raw receipts, including failed requests. Resume uses cached URLs."""
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript('''
          CREATE TABLE IF NOT EXISTS raw(id INTEGER PRIMARY KEY, url TEXT UNIQUE,
            url_sha256 TEXT, received_at TEXT, status INTEGER, headers TEXT,
            body BLOB, body_sha256 TEXT, bytes INTEGER, error TEXT);
          CREATE TRIGGER IF NOT EXISTS raw_no_update BEFORE UPDATE ON raw
            BEGIN SELECT RAISE(ABORT,'immutable raw'); END;
          CREATE TRIGGER IF NOT EXISTS raw_no_delete BEFORE DELETE ON raw
            BEGIN SELECT RAISE(ABORT,'immutable raw'); END;
        ''')

    def fetch(self, endpoint, **params):
        url = BASE + endpoint + '?' + urllib.parse.urlencode(sorted(params.items()))
        row = self.conn.execute('SELECT id,status,body,body_sha256,error FROM raw WHERE url=?', (url,)).fetchone()
        if row:
            ident, status, body, expected, error = row
            if digest(body) != expected:
                raise ValueError('Cached raw hash mismatch')
            if status != 200 or error:
                raise RuntimeError(f'Cached failed receipt {ident}: {status} {error}')
            return ident, json.loads(body)
        count, size = self.conn.execute('SELECT count(*),coalesce(sum(bytes),0) FROM raw').fetchone()
        if self.conn.execute('SELECT 1 FROM raw WHERE status IN (403,429) OR error IS NOT NULL').fetchone():
            raise RuntimeError('Prior failure prohibits further network access')
        if count >= MAX_REQUESTS or size >= MAX_BYTES:
            raise RuntimeError('Acquisition budget exhausted')
        time.sleep(1.1)
        request = urllib.request.Request(url, headers={'User-Agent': 'MLBHistoricalResearchPilot/1.0', 'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        body, headers, status, error = b'', {}, 0, None
        try:
            # Redirects disabled: never silently leave the anonymous official source.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(NoRedirect())
            try:
                response = opener.open(request, timeout=45)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.status
                headers = dict(response.headers)
                remaining = MAX_BYTES - size
                chunks = []
                while remaining:
                    chunk = response.read(min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                body = b''.join(chunks)
                if remaining == 0:
                    error = 'Body cap reached; response may be truncated'
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        cur = self.conn.execute('INSERT INTO raw VALUES(NULL,?,?,?,?,?,?,?,?,?)',
            (url, digest(url.encode()), datetime.now(timezone.utc).isoformat(), status,
             json.dumps(headers), body, digest(body), len(body), error))
        self.conn.commit()
        print(json.dumps(dict(receipt=cur.lastrowid, status=status, bytes=len(body), url=url)), flush=True)
        if status != 200 or error:
            raise RuntimeError(f'Acquisition stopped: HTTP {status}: {error}')
        return cur.lastrowid, json.loads(body)


def bulk_params(group, start, end, offset=0):
    return dict(stats='byDateRange', group=group, sportIds=1, gameType='R',
                startDate=start, endDate=end, season=start[:4], playerPool='ALL',
                limit=1000, offset=offset)


FIELDS = ('strikeOuts', 'baseOnBalls', 'hitByPitch', 'homeRuns', 'hits', 'atBats',
          'sacBunts', 'sacFlies', 'catchersInterference')
PERIODS = [('2024-01-01', '2024-12-31'), ('2025-01-01', '2025-05-31')]


def normalize(stat, group):
    denom = 'plateAppearances' if group == 'hitting' else 'battersFaced'
    missing = [key for key in (*FIELDS, denom) if type(stat.get(key)) is not int]
    if missing:
        return dict(valid=False, missing=missing)
    n, k, bb, hbp, hr, hits = (stat[key] for key in
        (denom, 'strikeOuts', 'baseOnBalls', 'hitByPitch', 'homeRuns', 'hits'))
    remainder = n - k - bb - hbp - hits
    delta = n - sum(stat[key] for key in
        ('atBats', 'baseOnBalls', 'hitByPitch', 'sacBunts', 'sacFlies', 'catchersInterference'))
    valid = all(stat[key] >= 0 for key in (*FIELDS, denom)) and hits >= hr and remainder >= 0 and delta == 0
    return dict(valid=valid, missing=[], denominator_name=denom, denominator=n,
        denominator_identity_delta=delta, K=k, BB=bb, HBP=hbp, HR=hr, hits=hits,
        BB_HBP=bb+hbp, otherhit=hits-hr, otherout_plus_residualreach=remainder,
        six_class_verified=False,
        definition_gap='No separate field_error and fielders_choice counts; remainder cannot be split safely.')


def validate_page(data, group, year):
    blocks = data.get('stats', [])
    if len(blocks) != 1:
        raise ValueError('Expected exactly one stats block')
    block = blocks[0]
    if block['type']['displayName'] != 'byDateRange' or block['group']['displayName'] != group:
        raise ValueError('Wrong stats type/group: possible season-total fallback')
    for split in block['splits']:
        if split.get('season') != year or type(split.get('player', {}).get('id')) is not int or split.get('sport', {}).get('id') != 1:
            raise ValueError('Invalid split season/player/sport identity')
    return block


def acquire(store):
    periods, history = [], {'hitting': {}, 'pitching': {}}
    for start, end in PERIODS:
        for group in history:
            offset, splits, receipts, total = 0, [], [], None
            while total is None or offset < total:
                receipt, data = store.fetch('stats', **bulk_params(group, start, end, offset))
                block = validate_page(data, group, start[:4])
                if total is not None and total != block['totalSplits']:
                    raise ValueError('Pagination total changed')
                total = block['totalSplits']
                page = block['splits']
                if not page or len(page) > 1000:
                    raise ValueError('Empty/oversized pagination page')
                splits.extend(page)
                receipts.append(receipt)
                offset += len(page)
            if len(splits) != total or len({s['player']['id'] for s in splits}) != total:
                raise ValueError('Incomplete pagination or duplicate player splits; do not double count team totals')
            denom = 'plateAppearances' if group == 'hitting' else 'battersFaced'
            audit = Counter()
            for split in splits:
                player, stat = split['player']['id'], split['stat']
                normalized = normalize(stat, group)
                audit['valid' if normalized['valid'] else 'invalid'] += 1
                for key in normalized['missing']:
                    audit['missing:' + key] += 1
                history[group].setdefault(player, []).append(dict(start=start, end=end,
                    normalized=normalized, stat=stat, receipts=receipts))
            # Highest-exposure player is fixed by denominator, not model performance.
            representative = max(splits, key=lambda s: (s['stat'].get(denom, 0), -s['player']['id']))
            player = representative['player']['id']
            receipt, log = store.fetch(f'people/{player}/stats', stats='gameLog', group=group,
                                      season=start[:4], gameType='R')
            log_blocks = log.get('stats', [])
            if len(log_blocks) != 1 or log_blocks[0]['type']['displayName'] != 'gameLog' or log_blocks[0]['group']['displayName'] != group:
                raise ValueError('Invalid game log response')
            entries = log_blocks[0]['splits']
            if any(e.get('gameType') != 'R' or e.get('player', {}).get('id') != player or e.get('sport', {}).get('id') != 1 for e in entries):
                raise ValueError('Game log player or gameType mismatch')
            if len({e['game']['gamePk'] for e in entries}) != len(entries):
                raise ValueError('Duplicate game log rows')
            selected = [e for e in entries if start <= e['date'] <= end]
            keys = (*FIELDS, denom)
            missing_log = sorted({key for e in selected for key in keys if type(e['stat'].get(key)) is not int})
            sums = {key: sum(e['stat'].get(key, 0) for e in selected) for key in keys}
            diffs = {key: sums[key] - representative['stat'].get(key, 0) for key in keys}
            verification = dict(player=player, receipt=receipt, selected_games=len(selected),
                selected_min=min((e['date'] for e in selected), default=None),
                selected_max=max((e['date'] for e in selected), default=None),
                dates_outside_requested_window=sum(not start <= e['date'] <= end for e in entries),
                full_season_denominator=sum(e['stat'].get(denom, 0) for e in entries),
                range_denominator=representative['stat'].get(denom), differences=diffs,
                missing_fields=missing_log, passed=bool(selected) and not missing_log and not any(diffs.values()))
            periods.append(dict(start=start, end=end, group=group, total_splits=total,
                receipts=receipts, normalization=dict(audit), sampled_date_verification=verification,
                response_echoes_exact_bounds=False))
            print(json.dumps(periods[-1]), flush=True)
    return history, periods


def coverage(source, gate_dir, history):
    before = filehash(source)
    sys.path.insert(0, str(gate_dir.resolve()))
    spec = importlib.util.spec_from_file_location('historical_gate', gate_dir / 'gate.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    rows, audit, games, hashes = gate.load(source)
    if filehash(source) != before:
        raise ValueError('Source changed')
    if sorted({r['day'] for r in rows}) != ['2025-06-01', '2025-06-02', '2025-06-03']:
        raise ValueError('Unexpected cache dates')
    target = {'hitting': set(), 'pitching': set()}
    # Include every cached roster/boxscore player, even players with no accepted PA.
    conn = gate.open_source(source)
    try:
        for (body,) in conn.execute('SELECT r.body FROM games g JOIN raw r ON g.raw_id=r.id'):
            feed = json.loads(body)
            for team in feed['liveData']['boxscore']['teams'].values():
                for p in team['players'].values():
                    for group in target:
                        target[group].add(p['person']['id'])
    finally:
        conn.close()
    available = {g: {p for p, records in players.items() if any(
        r['normalized']['valid'] and r['normalized']['denominator'] > 0 for r in records)}
        for g, players in history.items()}
    result = dict(input_sha256=before, games=len(games), accepted_pa=len(rows), extraction_audit=dict(audit),
        gate_code_sha256={p.name: filehash(p) for p in (gate_dir / 'gate.py', gate_dir / 'reconcile.py')},
        raw_body_sha256=hashes, by_role={}, all_cached_player_ids=sorted(target['hitting']),
        unresolved_boxscore_player_differences=sum(bool(r['player_differences']) for game in games for r in game['reconciliation']))
    predictions = [r for r in gate.evaluate(rows) if r['model'] == 'league' and r['phase'] == 'evaluation']
    result['evaluation_pa'] = len(predictions)
    for group, role in [('hitting', 'batter'), ('pitching', 'pitcher')]:
        appeared = {r[role] for r in rows}
        cached = target[group] | appeared
        missing_before = [r for r in predictions if r[role + '_history'] == 0]
        missing_after = [r for r in missing_before if r[role] not in available[group]]
        result['by_role'][role] = dict(accepted_pa_unique_players=len(appeared),
            accepted_pa_players_with_history=len(appeared & available[group]),
            missing_accepted_pa_player_ids=sorted(appeared - available[group]),
            all_cached_ids=len(cached), all_cached_ids_with_role_history=len(cached & available[group]),
            missing_all_cached_ids=sorted(cached - available[group]),
            evaluation_missing_before=len(missing_before), evaluation_missing_after=len(missing_after),
            evaluation_pa_newly_covered=len(missing_before)-len(missing_after),
            evaluation_missing_after_player_ids=sorted({r[role] for r in missing_after}))
    return result, target


def run(store, source, gate_dir, output):
    history, periods = acquire(store)
    cov, targets = coverage(source, gate_dir, history)
    count, size = store.conn.execute('SELECT count(*),sum(bytes) FROM raw').fetchone()
    passed = all(p['sampled_date_verification']['passed'] for p in periods)
    meta = dict(requests=count, response_body_bytes=size, periods=periods, coverage=cov,
        sampled_date_checks_passed=passed, point_in_time_proved=False,
        diagnostic_rerun=False, reason='Aggregate remainder cannot distinguish otherout/residualreach.',
        code_sha256=filehash(__file__), source=str(source.resolve()), gate_dir=str(gate_dir.resolve()))
    store.conn.executescript('''CREATE TABLE IF NOT EXISTS analysis(id INTEGER PRIMARY KEY,received_at TEXT,detail TEXT);
        CREATE TRIGGER IF NOT EXISTS analysis_no_update BEFORE UPDATE ON analysis BEGIN SELECT RAISE(ABORT,'immutable analysis'); END;
        CREATE TRIGGER IF NOT EXISTS analysis_no_delete BEFORE DELETE ON analysis BEGIN SELECT RAISE(ABORT,'immutable analysis'); END;
        CREATE TABLE IF NOT EXISTS player_history(player INTEGER,role TEXT,start TEXT,end TEXT,detail TEXT,PRIMARY KEY(player,role,start,end));
        CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON player_history BEGIN SELECT RAISE(ABORT,'immutable history'); END;
        CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON player_history BEGIN SELECT RAISE(ABORT,'immutable history'); END;''')
    for group, players in history.items():
        for player, records in players.items():
            if player in targets[group]:
                for record in records:
                    store.conn.execute('INSERT OR IGNORE INTO player_history VALUES(?,?,?,?,?)',
                        (player, group, record['start'], record['end'], json.dumps(record, sort_keys=True)))
    store.conn.execute('INSERT INTO analysis VALUES(NULL,?,?)', (datetime.now(timezone.utc).isoformat(), json.dumps(meta, sort_keys=True)))
    store.conn.commit()
    result = output / 'coverage.json'
    with result.open('x', encoding='utf-8') as stream:
        json.dump(meta, stream, indent=2, sort_keys=True)
    lines = ['# MLB historical player acquisition pilot', '',
        f'Executed {count} requests; {size:,} response-body bytes (limits 50 / 100,000,000).', '',
        'Official anonymous MLB StatsAPI only. No scheduling, production changes, payments, or bypass. '
        'An initial statsByDateRange HTTP 400 is retained; byDateRange is the accepted bulk type.', '',
        '## Date bounds and provenance', '',
        'Requested regular-season MLB (sportIds=1, gameType=R) for 2024-01-01 through 2024-12-31 '
        'and 2025-01-01 through 2025-05-31, separately. Response type, season, sport and player IDs '
        'are validated, pagination totals reconciled, duplicate player IDs rejected. '
        'The bulk response does NOT echo exact date bounds or gameType. One highest-exposure player '
        'per role/window is independently compared against dated regular-season game logs for every count field. '
        'This is sampled date-filter validation, not proof for every split. Historical data retrieved now '
        'may contain later corrections: point-in-time availability is NOT proved.', '',
        f'All four sampled checks passed: {passed}.', '']
    for p in periods:
        v = p['sampled_date_verification']
        lines.append(f"- {p['group']} {p['start']}–{p['end']}: {p['total_splits']} unique splits; normalization {p['normalization']}; "
            f"sample player {v['player']}: {v['selected_games']} games ({v['selected_min']}–{v['selected_max']}), "
            f"range/full-season denominator {v['range_denominator']}/{v['full_season_denominator']}, "
            f"outside-window log rows {v['dates_outside_requested_window']}, count deltas {v['differences']}.")
    lines += ['', '## Coverage', '', f"Cache: {cov['games']} games, {cov['accepted_pa']} accepted PA; evaluation {cov['evaluation_pa']} PA (June 2–3).", '',
        'Coverage means positive, internally reconciled PA/BF in either historical window, not verified six-class model history. '
        'All boxscore/roster IDs are retained; role-specific missingness across all IDs includes players who never bat or pitch.']
    for role, c in cov['by_role'].items():
        lines.append(f"- {role}: {c['accepted_pa_players_with_history']}/{c['accepted_pa_unique_players']} PA-participating IDs with history; "
            f"evaluation missing {c['evaluation_missing_before']}/{cov['evaluation_pa']} → {c['evaluation_missing_after']}/{cov['evaluation_pa']}; "
            f"newly covered {c['evaluation_pa_newly_covered']}. Missing participating IDs: {c['missing_accepted_pa_player_ids']}. "
            f"All cached IDs with this role history: {c['all_cached_ids_with_role_history']}/{c['all_cached_ids']}.")
    lines += ['', '## Denominators and diagnostic decision', '',
        'Batter denominator = plateAppearances; pitcher denominator = battersFaced (not innings or outs). '
        'Require denominator = atBats + baseOnBalls + hitByPitch + sacBunts + sacFlies + catchersInterference. '
        'BB includes intentional walks; do not add intentionalWalks twice. Preserve K/BB/HBP/HR/hits separately. '
        'Known classes are K, BB+HBP, HR, hits−HR. The remaining PA/BF reconciles arithmetically but combines '
        'otherout with residualreach. Ground/air outs cannot safely recover the existing event categories; '
        'separate field_error and fielders_choice counts are absent. No six-class counts or scores are invented.', '',
        f"PA diagnostic NOT rerun due to the aggregate definition gap. Cached team-side player-attribution differences: {cov['unresolved_boxscore_player_differences']}. "
        'Thirty sampled games on three dates also remain insufficient to establish model improvement.', '',
        '## Artifacts and integrity', '',
        f"Source SQLite SHA-256: `{cov['input_sha256']}` (unchanged before/after).",
        f"Pilot code SHA-256: `{meta['code_sha256']}`.",
        f"Corrected PA dependency hashes: `{json.dumps(cov['gate_code_sha256'], sort_keys=True)}`.",
        'Private history.sqlite contains exact raw response bytes, HTTP status/headers, UTC receipt time, '
        'URL and URL/body SHA-256, normalized per-player histories and analysis. Raw/analysis/history rows '
        'reject UPDATE/DELETE; private output is ignored by Git. This is application-level append-only '
        'integrity, not external WORM storage. coverage.json retains ID missingness and detailed audit. '
        'The collector counts cached failed receipts toward the budget, pauses 1.1 seconds between requests, '
        'disables redirects, and stops after 403/429 or transport/body-limit failures. HTTP headers/TLS overhead '
        'are not included in the response-body byte count.', '',
        'Sources: [official StatsAPI bulk endpoint](https://statsapi.mlb.com/api/v1/stats) '
        'and [official StatsAPI player stats endpoint](https://statsapi.mlb.com/api/v1/people). '
        'Exact parameterized source URLs are stored in private SQLite.', '']
    with (output / 'report.md').open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines))
    print(json.dumps(dict(requests=count, bytes=size, evaluation_pa=cov['evaluation_pa'],
        missing_after={role: c['evaluation_missing_after'] for role, c in cov['by_role'].items()},
        sampled_date_checks_passed=passed), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--gate-dir', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    store = Store(args.output / 'history.sqlite')
    try:
        if not args.probe:
            if not args.source or not args.gate_dir:
                parser.error('--source and --gate-dir required for acquisition+coverage')
            run(store, args.source, args.gate_dir, args.output)
            return
        ident, data = store.fetch('stats', **bulk_params('hitting', '2025-01-01', '2025-05-31'))
        print(json.dumps(dict(receipt=ident, top_keys=list(data), stats=[
            dict(meta={k:v for k,v in block.items() if k != 'splits'},
                 count=len(block.get('splits', [])), first=block.get('splits', [])[:1])
            for block in data.get('stats', [])]), indent=2))
    finally:
        store.conn.close()


if __name__ == '__main__':
    main()
