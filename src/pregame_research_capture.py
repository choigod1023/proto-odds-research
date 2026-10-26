"""Opt-in research capture; production SQLite is opened read-only, never initialized."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from player_fixture_time import fixture_time

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
MAX_FIXTURES = 512
MAX_SOURCE_ROWS = 4096
DOCUMENT_BYTES = 16 * 1024 * 1024
ARTIFACT_BYTES = 32 * 1024 * 1024
RESEARCH_BYTES = 64 * 1024 * 1024
CAPTURE_BYTES = 48 * 1024 * 1024
SETTLEMENT_PAIRS_PER_RUN = 100


def canonical_team(league, name, mapping):
    # Deliberately avoid importing player_info and all collector dependencies.
    compact = lambda value: re.sub(r'\s+', '', str(value))
    aliases = {compact(k): v for k, v in mapping.get(league, {}).items()}
    value = compact(aliases.get(compact(name), name))
    # Same supplemental aliases as player_info; no collector imports here.
    extras = {'MLB': {'애슬레틱': '애슬레틱스', '오클애슬': '애슬레틱스',
                     '오클랜드': '애슬레틱스', '시카고화이트삭스': '시카고W'},
              'NPB': {'소프트뱅': '소프트뱅크', '요코베이': '요코하마', '히로카프': '히로시마'}}
    return extras.get(league, {}).get(value, value)


def _storage_guard(store, *, results=False):
    path = getattr(store, 'path', None)
    if path is None:  # In-memory test stores have no filesystem allocation.
        return
    path = Path(path)
    total = sum(p.stat().st_size for p in (path, Path(str(path)+'-wal'),
                                         Path(str(path)+'-shm'), Path(str(path)+'-journal')) if p.exists())
    if total >= (RESEARCH_BYTES if results else CAPTURE_BYTES) - 1024 * 1024:
        raise ValueError('research_size_limit')
    if shutil.disk_usage(path.parent).free < 128 * 1024 * 1024:
        raise ValueError('research_disk_reserve')


def _payload_allowed(connection, table, name, cap, counters):
    size = connection.execute(f'SELECT length(CAST(payload_json AS BLOB)) FROM {table} WHERE name=?',
                              (name,)).fetchone()
    if size and size[0] > cap:
        counters['oversized_' + name] += 1
        return False
    return True


def _stamp(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.astimezone(UTC) if value.tzinfo else None
    except (ValueError, TypeError):
        return None


@contextmanager
def runtime_reader(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')  # Size checks and JSON scans share the same read snapshot.
        deadline = time.monotonic() + 4
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        yield connection
    finally:
        connection.close()


def _projection(fields, expression='g.value'):
    # Only internal constant field names are passed here.
    return 'json_object(' + ','.join(
        f"'{key}',json_extract({expression},'$.{key}')" for key in fields) + ')'


def _key(row, mapping, *, source=False):
    league = row.get('league')
    home = row.get('home_team' if source else 'home')
    away = row.get('away_team' if source else 'away')
    if not all(isinstance(v, str) and v.strip() for v in (league, home, away)):
        return None
    if source:
        kickoff = fixture_time(row.get('game_datetime'), league, row.get('source'))
    else:
        kickoff = _stamp(row.get('kickoff'))
        if kickoff is None:
            match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', str(row.get('date') or ''))
            try:
                kickoff = datetime(int(row['year']), *map(int, match.groups()), tzinfo=KST) if match else None
            except (ValueError, TypeError, KeyError):
                kickoff = None
    if kickoff is None:
        return None
    return (league, canonical_team(league, home, mapping),
            canonical_team(league, away, mapping), kickoff.astimezone(UTC).isoformat())


def _state(status, side):
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        if status.get('stale'):
            return 'stale'
        sides = status.get('side_states')
        state = sides.get(side, status.get('state')) if isinstance(sides, dict) else status.get('state')
        return state if isinstance(state, str) else None
    return None


def typed_facts(row, *, now=None, counters=None):
    """No absent-player inference and no conversion of a season roster to a XI."""
    facts = []
    def side_value(field, side):
        value = row.get(field)
        return value.get(side) if isinstance(value, dict) else None
    for side in ('home', 'away'):
        starter = side_value('starters', side)
        state = _state(row.get('starter_status'), side)
        if (isinstance(starter, dict) and starter.get('name') and not starter.get('stale')
                and state not in {'stale', 'fetch_failed', 'unknown'}
                and (starter.get('announced') is True or state in {'announced', 'confirmed', 'official_today'})):
            facts.append({'kind': 'starter', 'side': side, 'name': str(starter['name']),
                          'status': state or 'announced'})
        lineup_state = _state(row.get('lineup_status'), side)
        # Explicit per-fixture confirmation is mandatory. Mere presence is not confirmation.
        confirmed = lineup_state in {'official_today', 'confirmed', 'official', 'announced'}
        for field, kind in (('lineups', 'lineup_player'), ('benches', 'bench_player'),
                            ('unavailable', 'unavailable_player')):
            players = side_value(field, side)
            if not isinstance(players, list) or (field != 'unavailable' and not confirmed):
                continue
            for player in players:
                if not isinstance(player, dict) or not player.get('name') or player.get('stale'):
                    continue
                if field == 'unavailable' and player.get('source_type') == 'official_roster':
                    observed = _stamp(player.get('observed_at'))
                    if not now or not observed or not timedelta(0) <= now - observed <= timedelta(hours=6):
                        if counters is not None:
                            counters['stale_or_missing_injury_source'] += 1
                        continue
                status = player.get('availability_status') or player.get('status') or player.get('reason')
                if field == 'unavailable' and (not isinstance(status, str) or not status or
                                               status in {'unknown', '미확인', '확인 중'}):
                    continue
                fact = {'kind': kind, 'side': side, 'name': str(player['name']),
                        'status': str(status) if field == 'unavailable' else lineup_state}
                for key in ('position', 'batting_order', 'order', 'player_id',
                            'availability_status', 'reason_code', 'source_type', 'reason'):
                    if isinstance(player.get(key), (str, int)):
                        fact[key] = player[key]
                if field == 'unavailable':
                    fact['observed_at'] = player.get('observed_at') or row.get('updated_at')
                    if isinstance(player.get('source_url'), str):
                        fact['source_url'] = player['source_url']
                facts.append(fact)
    return facts


def baseline(connection, game, kickoff, now):
    snapshot = game.get('snapshot_id')
    selection = game.get('selection_id')
    if not snapshot or not selection:
        return None
    fields = ('snapshot_id', 'event_id', 'as_of', 'kickoff', 'market_observed_at', 'captured_at', 'predictions')
    row = connection.execute(
        f"SELECT {_projection(fields, 'record_json')}, "
        "json_extract(record_json,'$.features.research_player_inputs.observed_at') FROM prediction_records "
        "WHERE identity=? AND record_type='prediction' AND length(CAST(record_json AS BLOB))<=2097152",
        (snapshot,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    pred = record.get('predictions') or {}
    if (record.get('snapshot_id') != snapshot or record.get('event_id') != game.get('event_id')
            or pred.get('selection_id') != selection or _stamp(record.get('kickoff')) != kickoff):
        return None
    as_of, captured, market = (_stamp(record.get(k)) for k in ('as_of', 'captured_at', 'market_observed_at'))
    if not (as_of and captured and market and market <= as_of <= captured <= now
            and as_of < kickoff - timedelta(minutes=30) and captured < kickoff
            and now - market <= timedelta(hours=2)):
        return None
    odds, probability = pred.get('odds'), pred.get('probability')
    if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (odds, probability)) or odds <= 1 or not 0 <= probability <= 1):
        return None
    return {'snapshot_id': snapshot, 'selection_id': selection, 'odds': odds,
            'probability': probability, 'as_of': as_of.isoformat(),
            'captured_at': captured.isoformat(), 'market_observed_at': market.isoformat(),
            'feature_observed_at': row[1],
            'frozen_at': None, 'freeze_cutoff_at': (kickoff-timedelta(minutes=30)).isoformat()}


def collect_rows(connection, now, counters):
    """Project JSON arrays inside SQLite; never materialize runtime documents/ledger."""
    # Do not call player_info._team_map(): in production it initializes the
    # writable RuntimeDatabase. Read only the compact alias entries instead.
    mapping = {}
    if not _payload_allowed(connection, 'documents', 'processed_team_map', DOCUMENT_BYTES, counters):
        return
    if not _payload_allowed(connection, 'artifacts', 'picks_v2', ARTIFACT_BYTES, counters):
        return
    aliases = connection.execute("""SELECT league.key,alias.key,alias.value
        FROM documents d,json_each(d.payload_json) league,json_each(league.value) alias
        WHERE d.name='processed_team_map' LIMIT 4097""")
    for count, (league, alias, value) in enumerate(aliases, 1):
        if count > 4096:
            raise ValueError('alias_limit')
        if isinstance(value, str):
            mapping.setdefault(league, {})[alias] = value
    fields = ('event_id', 'sport', 'league', 'home', 'away', 'year', 'round', 'date', 'status', 'kickoff')
    projected = _projection(fields)
    cursor = connection.execute(f"""SELECT {projected},
        COALESCE(json_extract(g.value,'$.prediction_revision_id'),json_extract(g.value,'$.decision_snapshot.decision_id')),
        COALESCE(json_extract(g.value,'$.decision_snapshot.selection_id'),json_extract(g.value,'$.prediction_record.selection_id'))
        FROM artifacts a,json_each(a.payload_json,'$.live') g WHERE a.name='picks_v2'""")
    fixtures = {}
    for count, row in enumerate(cursor, 1):
        if count > MAX_FIXTURES:
            raise ValueError('fixture_limit')
        game = json.loads(row[0])
        game.update(snapshot_id=row[1], selection_id=row[2])
        key = _key(game, mapping)
        if key is None or not game.get('event_id'):
            counters['invalid_fixture'] += 1
            continue
        kickoff = _stamp(key[3])
        if game.get('status') not in {'경기전', '배당대기'} or now >= kickoff - timedelta(minutes=30):
            counters['not_before_t30'] += 1
            continue
        if key in fixtures:
            counters['ambiguous_fixture'] += 1
            fixtures[key] = None
        else:
            fixtures[key] = game
    sources = defaultdict(list)
    fields = ('league', 'home_team', 'away_team', 'game_datetime', 'updated_at', 'source',
              'source_url', 'game_id', 'starters', 'starter_status', 'lineups', 'lineup_status',
              'unavailable', 'benches', 'stale', 'injuries_refreshed_at')
    allowed = [name for name in ('player_info', 'kbo_pregame')
               if _payload_allowed(connection, 'documents', name, DOCUMENT_BYTES, counters)]
    cursor = connection.execute(f"""SELECT d.name,{_projection(fields)},
        json_extract(d.payload_json,'$.injuries_refreshed_at')
        FROM documents d,json_each(d.payload_json,'$.games') g
        WHERE d.name IN ({','.join('?' for _ in allowed)})""", allowed)
    for count, row in enumerate(cursor, 1):
        if count > MAX_SOURCE_ROWS:
            raise ValueError('source_limit')
        source = json.loads(row[1])
        source['injuries_refreshed_at'] = source.get('injuries_refreshed_at') or row[2]
        key = _key(source, mapping, source=True)
        if key in fixtures and fixtures[key] is not None:
            bucket = sources[(key, row[0])]
            if len(bucket) < 2:
                bucket.append(source)
    for key, game in fixtures.items():
        if game is None:
            continue
        kickoff = _stamp(key[3])
        try:
            reference = baseline(connection, game, kickoff, now)
        except (ValueError, TypeError, AttributeError):
            counters['malformed_baseline'] += 1
            reference = None
        if reference is None:
            counters['missing_baseline'] += 1
        found = False
        for name in ('player_info', 'kbo_pregame'):
            matches = sources.get((key, name), [])
            if len(matches) > 1:
                found = True
                counters['ambiguous_source'] += 1
                continue
            if not matches:
                continue
            found = True
            source = matches[0]
            updated = _stamp(source.get('updated_at'))
            if (updated is None or not isinstance(source.get('source'), str)
                    or not source.get('source') or not source.get('game_id')):
                counters['missing_provenance'] += 1
                continue
            if updated > now:
                counters['future_source'] += 1
                continue
            if source.get('stale') or now - updated > timedelta(hours=6):
                counters['stale_source'] += 1
                continue
            facts = typed_facts(source, now=now, counters=counters)
            if not facts:
                counters['no_typed_facts'] += 1
                continue
            yield {'event_id': game['event_id'], 'sport': game['sport'], 'league': key[0],
                   'round': game.get('round'),
                   'home': key[1], 'away': key[2], 'kickoff': key[3], 'observed_at': now.isoformat(),
                   'source_updated_at': updated.isoformat(), 'source': source['source'],
                   'source_document': name, 'source_game_id': source['game_id'],
                   'source_url': source.get('source_url'), 'facts': facts, 'baseline': reference}
        if not found:
            counters['missing_source'] += 1


def _cursor(store, value=None):
    if not hasattr(store, 'path'):
        if value is not None:
            store._capture_cursor = value
        return getattr(store, '_capture_cursor', 0)
    path = Path(str(store.path) + '.cursor')
    if value is None:
        try:
            if path.stat().st_size > 1024:
                return 0
            return max(0, int(json.loads(path.read_text())['after_id']))
        except (OSError, ValueError, KeyError, TypeError):
            return 0
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False,
                                     prefix=path.name+'.', suffix='.tmp') as out:
        json.dump({'after_id': value}, out)
        temp = out.name
    try:
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return value


def _append(store, kind, counters, **kwargs):
    try:
        _storage_guard(store, results=kind == 'result')
        return getattr(store, 'append_' + kind)(**kwargs)
    except Exception as exc:
        counters[kind + '_write_errors'] += 1
        if isinstance(exc, ValueError) and str(exc) == 'research_size_limit':
            counters['needs_archive' if kind == 'result' else 'capture_size_limit'] += 1
        return None


def settle_pairs(connection, store, now, counters):
    """Only an explicitly matching official snapshot/selection can settle a pair."""
    after = _cursor(store)
    deadline = time.monotonic() + 4
    processed = 0
    # Keyset pagination retains bounded memory, including games no longer on the dashboard.
    while True:
        pairs = list(store.iter_pairs(after_id=after, limit=100))
        if not pairs:
            _cursor(store, 0)  # Cycle to revisit pending games and later official corrections.
            return
        for pair in pairs:
            if time.monotonic() > deadline or processed >= SETTLEMENT_PAIRS_PER_RUN:
                counters['settlement_budget_exhausted'] += 1
                return
            after = pair['id']
            _cursor(store, after)  # Persist before costly lookup, including interrupted SQL.
            processed += 1
            fields = ('snapshot_id', 'settled_at', 'captured_at', 'source', 'outcome')
            rows = connection.execute(f"""SELECT {_projection(fields, 'record_json')}
                FROM prediction_records WHERE event_id=? AND record_type='settlement'
                AND length(CAST(record_json AS BLOB))<=2097152
                AND snapshot_id=? AND json_extract(record_json,'$.source.name')='proto_official'
                ORDER BY ledger_sequence DESC""", (pair['event_id'], pair['snapshot_id']))
            for row in rows:
                record = json.loads(row[0])
                outcome = record.get('outcome') or {}
                settled, captured = (_stamp(record.get(k)) for k in ('settled_at', 'captured_at'))
                kickoff = _stamp(pair['kickoff'])
                if (record.get('snapshot_id') != pair['snapshot_id']
                        or outcome.get('selection_id') != pair['selection_id']
                        or outcome.get('result') not in {'hit', 'miss', 'void'}
                        or not settled or not captured or not kickoff
                        or not kickoff <= settled <= captured <= now):
                    counters['rejected_settlement'] += 1
                    continue
                stored = _append(store, 'result', counters, event_id=pair['event_id'], kickoff=pair['kickoff'],
                    settled_observed_at=now.isoformat(), source=record['source'],
                    snapshot_id=pair['snapshot_id'], selection_id=pair['selection_id'],
                    outcome=outcome, source_updated_at=settled.isoformat())
                if stored is not None:
                    counters['results_processed'] += 1
                break


def capture_once(runtime_path, store, *, now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('aware_clock_required')
    now = now.astimezone(UTC)
    counters = Counter()
    # Existing official joins have priority even if new evidence is malformed/oversized.
    try:
        with runtime_reader(runtime_path) as connection:
            settle_pairs(connection, store, now, counters)
    except Exception:
        counters['settlement_stage_errors'] += 1
    try:
        _capture_stage(runtime_path, store, now, counters)
    except Exception:
        counters['capture_stage_errors'] += 1
    partial = any('errors' in k or 'limit' in k or k == 'needs_archive' or k.startswith('oversized_')
                  for k in counters)
    return {'status': 'partial' if partial else 'ok', **dict(counters)}


def _capture_stage(runtime_path, store, now, counters):
    with runtime_reader(runtime_path) as connection:
        pending = {}
        for row in collect_rows(connection, now, counters):
            source = {'name': row['source_document'] + ':' + row['source'], 'document': row['source_document'],
                      'game_id': row['source_game_id'], 'url': row['source_url']}
            kinds = {'starter': 'starters', 'lineup_player': 'lineups',
                     'bench_player': 'benches', 'unavailable_player': 'unavailable'}
            facts = [{'type': kinds[f['kind']], 'subject': f['side'] + ':' + f['name'],
                      'value': {k: v for k, v in f.items() if k not in {'kind', 'side'}}}
                     for f in row['facts']]
            evidence = _append(store, 'evidence', counters, event_id=row['event_id'], kickoff=row['kickoff'],
                observed_at=row['observed_at'], source=source, typed_facts=facts,
                source_updated_at=row['source_updated_at'])
            if evidence is None:
                continue
            reference = row['baseline']
            counters['evidence_processed'] += 1
            if reference is None:
                counters['evidence_without_pair'] += 1
                continue
            identity = (row['event_id'], reference['snapshot_id'], reference['selection_id'])
            item = pending.setdefault(identity, {'row': row, 'refs': []})
            item['refs'].append({'id': evidence['id'], 'datahash': evidence['datahash']})
        for item in pending.values():
            row = item['row']
            reference = row['baseline']
            decision = {k: row[k] for k in ('sport', 'league', 'home', 'away', 'round')}
            decision.update({k: reference[k] for k in ('probability', 'as_of', 'captured_at',
                                                     'frozen_at', 'freeze_cutoff_at', 'feature_observed_at')})
            # These are the baseline's clocks. New research feature observation
            # is retained separately in evidence.observed_at, not in immutable pair content.
            pair = _append(store, 'pair', counters, event_id=row['event_id'], kickoff=row['kickoff'],
                observed_at=row['observed_at'], source={'name': 'runtime_prediction_ledger'},
                snapshot_id=reference['snapshot_id'], selection_id=reference['selection_id'],
                odds={'decimal': reference['odds'], 'observed_at': reference['market_observed_at']},
                baseline_decision=decision, source_updated_at=reference['as_of'],
                evidence_refs=item['refs'])
            if pair is not None:
                counters['captures_processed'] += 1


def _worker_unlocked():
    research = Path(os.environ['PROODD_RESEARCH_DB_PATH']).resolve()
    total = sum(p.stat().st_size for p in (research, Path(str(research)+'-wal'),
                                         Path(str(research)+'-shm'), Path(str(research)+'-journal')) if p.exists())
    if total >= RESEARCH_BYTES:
        return {'status': 'skipped', 'reason': 'needs_archive'}
    if shutil.disk_usage(research.parent).free < 128 * 1024 * 1024:
        return {'status': 'skipped', 'reason': 'research_disk_reserve'}
    from pregame_research_store import PregameResearchStore
    store = PregameResearchStore(research, official_sources={'proto_official'})
    return capture_once(os.environ['PROODD_DB_PATH'], store)


def _worker():
    # OS lock is released on timeout/kill; no stale O_EXCL lock can strand capture.
    path = Path(os.environ['PROODD_RESEARCH_DB_PATH']).resolve()
    with open(str(path) + '.capture.lock', 'a+b') as lock:
        try:
            if os.name == 'nt':
                import msvcrt
                if lock.seek(0, 2) == 0:
                    lock.write(b'0')
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {'status': 'skipped', 'reason': 'capture_busy'}
        return _worker_unlocked()


def _safe_capture():
    """No-op by default; research errors must never interrupt production publication."""
    research_path = os.environ.get('PROODD_RESEARCH_DB_PATH')
    if not research_path:
        return {'status': 'disabled'}
    try:
        runtime_path = os.environ.get('PROODD_DB_PATH')
        if not runtime_path:
            return {'status': 'skipped', 'reason': 'missing_runtime_path'}
        runtime = Path(runtime_path).resolve()
        research = Path(research_path).resolve()
        if (runtime == research or (runtime.exists() and research.exists()
                                   and os.path.samefile(runtime, research))):
            return {'status': 'error', 'reason': 'database_paths_overlap'}
        if sys.platform.startswith('linux'):
            try:
                text = Path('/proc/meminfo').read_text()
                available = int(re.search(r'^MemAvailable:\s+(\d+)\s+kB', text, re.M)[1])
            except (OSError, TypeError, ValueError):
                return {'status': 'skipped', 'reason': 'memory_available_unknown'}
            if available < 256 * 1024:
                return {'status': 'skipped', 'reason': 'memory_available_below_256mib'}
        # Hard deadline includes research DB lock waits and Python processing.
        child = subprocess.run([sys.executable, '-c',
            'import json; from pregame_research_capture import _worker; print(json.dumps(_worker()))'],
            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=12)
        if child.returncode:
            return {'status': 'error', 'reason': 'research_worker_failed'}
        return json.loads(child.stdout)
    except Exception as exc:
        # Class only: never emit SQL, filesystem paths, payloads or credentials.
        return {'status': 'error', 'reason': type(exc).__name__}


def safe_capture():
    summary = _safe_capture()
    if summary['status'] != 'disabled':
        print('[pregame_research_capture] ' + json.dumps(summary, ensure_ascii=True), flush=True)
    return summary
