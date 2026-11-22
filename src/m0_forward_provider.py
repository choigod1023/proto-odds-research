"""Read validated actual-M0 captures without collecting or training.

``root`` is the research directory, not a production data override from the
environment. The default points at the collector's persistent server volume.
"""
from datetime import datetime, timedelta, timezone
import base64
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import threading

# Production starts scripts from src/; the research package lives one level up.
_repo = str(Path(__file__).resolve().parents[1])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from research.kbo_forward import ledger
from research.kbo_forward.official import SourceError
from research.m1_service import model, observer

ROOT = Path('/data/research')
MAX_BYTES = 8 * 1024 * 1024
MAX_CAPTURES = 20
MAX_INDEX_BYTES = 256 * 1024
KST = timezone(timedelta(hours=9))
_lock = threading.Lock()
_cache = {}
_index_cache = {}


def _time(value):
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError('timezone required')
        return value.astimezone(timezone.utc)
    return observer.moment(value)


def _same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (float, int)) and not isinstance(a, bool) and isinstance(b, (float, int)) and not isinstance(b, bool):
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
    return type(a) is type(b) and a == b


def _stat(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _verify_kbo_league(payload, archive_root):
    # The observer reconstructs the official event and lineups but does not
    # itself enforce LE_ID=1. Inspect the same verified schedule bytes here.
    from research.kbo_forward.official import SCHEDULE
    body = payload['evidence']['announcement']['body']
    base = Path(archive_root).resolve()
    path = (base / body['relative_file']).resolve()
    wanted = body['archived_canonical_bundle_sha256']
    if not path.is_relative_to(base / 'verified-bundles') or path.name != wanted + '.json.gz':
        raise ValueError('invalid source archive path')
    raw = gzip.decompress(path.read_bytes())
    if observer.digest(raw) != wanted:
        raise ValueError('source archive hash mismatch')
    bundle = json.loads(raw)
    games = [game for response in bundle['responses'] if response['receipt']['url'] == SCHEDULE
             for game in json.loads(base64.b64decode(response['body_base64'], validate=True))['game']
             if game.get('G_ID') == payload['event']['official_game_id']]
    if len(games) != 1 or str(games[0].get('LE_ID')) != '1':
        raise ValueError('official KBO league identity mismatch')


def _verified_capture(record, root, meta):
    if record.get('schema') != 'm1-pregame-rehearsal-v1' or record.get('type') != 'capture':
        raise ValueError('unsupported capture')
    if any(record.get(k) is not False for k in ('promoted', 'production_policy_changed', 'paper_wallet_changed')):
        raise ValueError('unexpected capture policy')
    captured = _time(record['captured_at'])
    if captured < _time(meta['all_coefficients_preserved_by']):
        raise ValueError('capture precedes model artifact')
    if record.get('model_metadata') != meta:
        raise ValueError('model metadata mismatch')
    payload = record['input']
    if observer.digest(observer.canonical(payload)) != record['input_sha256']:
        raise ValueError('input hash mismatch')
    unpriced = not record['roi_eligible']
    if type(record['roi_eligible']) is not bool or unpriced != ('market' not in payload):
        raise ValueError('market status mismatch')
    ledger.validate_model_snapshot(payload, captured, unpriced=unpriced)
    for evidence in payload['evidence'].values():
        if _time(evidence['received_at']) > captured:
            raise ValueError('future source receipt')
    event, row = payload['event'], payload['row']
    kickoff = _time(event['kickoff_at'])
    local_date = kickoff.astimezone(KST).date()
    if (captured > kickoff - timedelta(minutes=30) or row['event_date'] != local_date.isoformat()
            or type(event['season']) is not int or event['season'] != local_date.year
            or row['season'] != event['season'] or row['home_id'] != event['home_id']
            or row['away_id'] != event['away_id'] or event['home_id'] == event['away_id']
            or event['home_id'] not in model.TEAM_NAMES or event['away_id'] not in model.TEAM_NAMES):
        raise ValueError('capture identity or time mismatch')
    for side in ('home', 'away'):
        roster = row['roster'][side]
        if (row['history_coverage'][side]['status'] != 'complete' or not isinstance(roster['hitters'], list)
                or len(roster['hitters']) != 9 or len(set(roster['hitters'])) != 9 or not roster['starter']):
            raise ValueError('incomplete announced roster or history')
    archive_root = root / 'kbo-m0-forward-v1' / 'official'
    observer._verify_archives(payload, archive_root, captured)
    _verify_kbo_league(payload, archive_root)
    if not unpriced:
        observer._verify_market_bytes(payload, root / 'kbo-m0-forward-v1' / 'prices' / 'price-evidence')
        if captured - _time(payload['evidence']['odds']['received_at']) > timedelta(minutes=10):
            raise ValueError('stale odds at capture')
        if any(captured >= _time(payload['market'][k]) for k in ('close_at', 'round_close_at')):
            raise ValueError('market closed at capture')
    operating_price = payload.get('operating_price_evidence')
    if operating_price is not None:
        from kbo_operating_prices import verify
        verify(root / 'kbo-m0-forward-v1' / 'operating-price-evidence', operating_price, event, captured)
    actual = record['models']['m0_actual']
    recomputed = model.forecast(row)
    if not _same(actual, recomputed['m0_actual']):
        raise ValueError('frozen actual M0 forecast mismatch')
    if any(not isinstance(actual[k], (int, float)) or isinstance(actual[k], bool)
           or not math.isfinite(actual[k]) or actual[k] <= 0 for k in ('home_rate', 'away_rate')):
        raise ValueError('invalid actual M0 rates')
    # Reuse the existing verification pass. A missing or altered research arm
    # cannot enter the comparison or break the already verified M0 capture.
    companions = {
        name: {'home_mean': arm['home_rate'], 'away_mean': arm['away_rate']}
        for name in ('m0_projected', 'm1_projected')
        if (arm := record['models'].get(name)) is not None and _same(arm, recomputed[name])
    }
    return {'event': event, 'captured': captured, 'capture_hash': record['hash'], 'companions': companions,
            'operating_price_evidence': operating_price,
            'artifact_hash': meta['bundle_sha256'], 'source_evidence_hash': observer.digest(observer.canonical(payload['evidence'])),
            'home_mean': actual['home_rate'], 'away_mean': actual['away_rate']}


def _read_bounded(path, maximum, description):
    before = _stat(path)
    if before[2] > maximum:
        raise ValueError(f'{description} exceeds size limit')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum or len(raw) != before[2] or _stat(path) != before:
        raise ValueError(f'{description} changed during read or exceeds size limit')
    return raw, before


def _index_entries(state, index):
    if index.is_symlink() or not index.is_file():
        raise ValueError('invalid capture index file')
    index_stat = _stat(index)
    key = str(index.resolve())
    cached = _index_cache.get(key)
    if cached and cached[0] == index_stat:
        return cached[1], index_stat
    raw, index_stat = _read_bounded(index, MAX_INDEX_BYTES, 'capture index')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema') != 'm0-capture-index-v1':
        raise ValueError('invalid capture index schema')
    entries = data.get('captures')
    if not isinstance(entries, list) or len(entries) > MAX_CAPTURES:
        raise ValueError('invalid capture index count')
    base = (state / 'capture-index').resolve()
    if not base.is_relative_to(state.resolve()):
        raise ValueError('capture directory outside state')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid capture index entry')
        digest = entry.get('hash')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or digest in seen:
            raise ValueError('invalid or duplicate indexed hash')
        seen.add(digest)
        if entry.get('file') != f'capture-index/{digest}.json' or not isinstance(entry.get('event'), dict):
            raise ValueError('invalid indexed path or event')
    _index_cache.clear()
    _index_cache[key] = (index_stat, entries)
    return entries, index_stat


def _load_index(root, state, index):
    entries, index_stat = _index_entries(state, index)
    files = []
    for entry in entries:
        path = state / entry['file']
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(state.resolve()):
            raise ValueError('indexed capture outside state or missing')
        files.append((path, _stat(path)))
    artifact_stat = _stat(model.PATH)
    fingerprint = ('index', index_stat, artifact_stat, tuple(stat for _, stat in files))
    key = str(root.resolve())
    cached = _cache.get(key)
    if cached and cached[0] == fingerprint:
        return cached[1]
    if hashlib.sha256(model.PATH.read_bytes()).hexdigest() != model.BUNDLE_SHA256:
        raise ValueError('frozen model artifact hash mismatch')
    meta = model.metadata()
    eligible = []
    for entry, (path, initial) in zip(entries, files):
        raw, current = _read_bounded(path, MAX_BYTES, 'indexed capture')
        if current != initial:
            raise ValueError('indexed capture changed during validation')
        record = json.loads(raw)
        if not isinstance(record, dict):
            raise ValueError('invalid indexed capture record')
        canonical_record = observer.canonical(record)
        if raw not in (canonical_record, canonical_record + b'\n'):
            raise ValueError('indexed capture is not canonical')
        core = dict(record)
        claimed = core.pop('hash', None)
        if (claimed != entry['hash'] or observer.digest(observer.canonical(core)) != claimed
                or not isinstance(core.get('previous_hash'), str)
                or not re.fullmatch('[0-9a-f]{64}', core['previous_hash'])
                or record.get('input', {}).get('event') != entry['event']):
            raise ValueError('indexed capture hash or event mismatch')
        # The source checks are independent of the original ledger chain.
        try:
            eligible.append(_verified_capture(record, root, meta))
        except (KeyError, TypeError, ValueError, OSError, OverflowError, AttributeError, EOFError, SourceError):
            continue
    if (_stat(index) != index_stat or _stat(model.PATH) != artifact_stat
            or any(_stat(path) != before for path, before in files)):
        raise ValueError('capture index changed during validation')
    _cache.clear()
    _cache[key] = (fingerprint, eligible)
    return eligible


def _load(root):
    state = root / 'kbo-m0-actual-forward-v1'
    index = state / 'capture-index.json'
    try:
        index.lstat()
    except FileNotFoundError:
        pass
    else:
        return _load_index(root, state, index)
    path = state / 'predictions.jsonl'
    before = _stat(path)
    artifact_stat = _stat(model.PATH)
    if before[2] > MAX_BYTES:
        raise ValueError('ledger exceeds 8 MiB limit')
    key = str(root.resolve())
    cached = _cache.get(key)
    if cached and cached[0] == ('ledger', before, artifact_stat):
        try:
            index.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError('capture index appeared during ledger lookup')
        return cached[1]
    # Bounded read first, then use the existing chain verifier on a private
    # snapshot. A concurrent append cannot make observer.read exceed the cap.
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('ledger exceeds 8 MiB limit')
    if _stat(path) != before or len(raw) != before[2]:
        raise ValueError('ledger changed during read')
    with tempfile.TemporaryDirectory(prefix='m0-ledger-') as directory:
        snapshot = Path(directory) / 'predictions.jsonl'
        snapshot.write_bytes(raw)
        records = observer.read(snapshot)
    # Check the bytes even if the model module's in-process bundle was cached.
    if hashlib.sha256(model.PATH.read_bytes()).hexdigest() != model.BUNDLE_SHA256:
        raise ValueError('frozen model artifact hash mismatch')
    meta = model.metadata()
    eligible = []
    for record in reversed(records):
        if record.get('type') != 'capture':
            continue
        try:
            eligible.append(_verified_capture(record, root, meta))
        except (KeyError, TypeError, ValueError, OSError, OverflowError, AttributeError, EOFError, SourceError):
            continue
        if len(eligible) == MAX_CAPTURES:
            break
    if _stat(path) != before or _stat(model.PATH) != artifact_stat:
        raise ValueError('ledger changed during validation')
    try:
        index.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('capture index appeared during ledger validation')
    # Keep memory bounded even when callers use several test roots.
    _cache.clear()
    _cache[key] = (('ledger', before, artifact_stat), eligible)
    return eligible


def get_distribution(game, as_of, built_at, *, root=None):
    """Return available actual-M0 means or an explicit unavailable reason.

    Game requires league='KBO', home_team_id, away_team_id and kickoff_at.
    An optional official_game_id, season or date further restricts the match.
    The source clock is the completed capture time, never a reconstructed time.
    """
    try:
        asof, built = _time(as_of), _time(built_at)
        kickoff = _time(game['kickoff_at'])
        date = kickoff.astimezone(KST).date()
        if (game.get('league') != 'KBO' or game['home_team_id'] not in model.TEAM_NAMES
                or game['away_team_id'] not in model.TEAM_NAMES or game['home_team_id'] == game['away_team_id']
                or ('season' in game and (type(game['season']) is not int or game['season'] != date.year))
                or ('date' in game and game['date'] != date.isoformat())
                or asof > built or built >= kickoff - timedelta(minutes=30)):
            raise ValueError('game identity or decision time invalid')
        with _lock:
            captures = _load(Path(root) if root is not None else ROOT)
            matches = [c for c in captures if c['captured'] <= asof
                       and c['event']['home_id'] == game['home_team_id']
                       and c['event']['away_id'] == game['away_team_id']
                       and c['event']['season'] == date.year
                       and _time(c['event']['kickoff_at']) == kickoff
                       and ('official_game_id' not in game or c['event']['official_game_id'] == game['official_game_id'])]
        if not matches:
            raise ValueError('no eligible actual M0 capture')
        if len({c['event']['official_game_id'] for c in matches}) != 1:
            raise ValueError('ambiguous official game identity')
        c = max(matches, key=lambda x: x['captured'])
        return {'status': 'available', 'model': 'm0_actual', 'home_mean': c['home_mean'],
                'away_mean': c['away_mean'], 'source_available_at': c['captured'].isoformat(),
                'capture_hash': c['capture_hash'], 'artifact_hash': c['artifact_hash'],
                'official_game_id': c['event']['official_game_id'], 'kickoff_at': c['event']['kickoff_at'],
                'scope': 'full_game', 'source_evidence_hash': c['source_evidence_hash'],
                **({'operating_price_evidence': c['operating_price_evidence']}
                   if c['operating_price_evidence'] is not None else {}),
                'companions': {name: dict(rates) for name, rates in c['companions'].items()}}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError, EOFError) as exc:
        return {'status': 'unavailable', 'reason': str(exc)}
