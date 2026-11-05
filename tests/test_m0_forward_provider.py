"""Synthetic captures only; no live source data or production assertions."""
from copy import deepcopy
import base64
from datetime import timedelta
import gzip
import json

import pytest

from research.kbo_forward import test_runner as helpers
from research.m1_service import observer
from src import m0_forward_provider as provider


@pytest.fixture
def capture(tmp_path, monkeypatch):
    provider._cache.clear()
    # Source reconstruction has dedicated tests; these are not real archives.
    checks = []
    monkeypatch.setattr(observer, '_verify_archives', lambda *args: checks.append('archive'))
    monkeypatch.setattr(observer, '_verify_market_bytes', lambda *args: checks.append('price'))
    monkeypatch.setattr(provider, '_verify_kbo_league', lambda *args: None)
    payload = helpers.payload('full_game_2way')
    payload['event'].update(home_id='SS', away_id='HT')
    payload['row'].update(home_id='SS', away_id='HT')
    payload['row']['history_coverage'] = {s: {'status': 'complete'} for s in ('home', 'away')}
    payload.pop('market'); payload['evidence'].pop('odds')
    now = helpers.ledger.moment(payload['event']['kickoff_at']) - timedelta(hours=2)
    path = tmp_path/'kbo-m0-actual-forward-v1'/'predictions.jsonl'
    recorder = observer.Recorder(path, helpers.ledger.validate_model_snapshot, clock=lambda: now)
    record = recorder.capture(payload, unpriced=True)
    event = payload['event']
    game = {'league': 'KBO', 'home_team_id': event['home_id'], 'away_team_id': event['away_id'],
            'kickoff_at': event['kickoff_at'], 'official_game_id': event['official_game_id'],
            'date': payload['row']['event_date'], 'season': event['season']}
    return tmp_path, path, record, game, now, checks


def query(c, game=None, asof=None, built=None):
    root, _, _, default_game, now, _ = c
    return provider.get_distribution(default_game if game is None else game,
                                     now if asof is None else asof,
                                     now if built is None else built, root=root)


def rewrite(path, record):
    record = deepcopy(record)
    record.pop('hash', None)
    record['hash'] = observer.digest(observer.canonical(record))
    path.write_bytes(observer.canonical(record)+b'\n')


def write_index(capture, record=None):
    root, _, original, _, _, _ = capture
    record = deepcopy(original if record is None else record)
    state = root/'kbo-m0-actual-forward-v1'
    folder = state/'capture-index'; folder.mkdir(exist_ok=True)
    digest = record['hash']
    (folder/f'{digest}.json').write_bytes(observer.canonical(record)+b'\n')
    entry = {'hash': digest, 'event': record['input']['event'], 'file': f'capture-index/{digest}.json'}
    index = state/'capture-index.json'
    index.write_bytes(observer.canonical({'schema': 'm0-capture-index-v1', 'captures': [entry]}))
    return index, folder/f'{digest}.json', entry


def put_index(path, entries, schema='m0-capture-index-v1'):
    path.write_bytes(observer.canonical({'schema': schema, 'captures': entries}))


def test_actual_capture_exact_identity_and_cached_verification(capture):
    root, path, record, game, now, checks = capture
    checks.clear()
    result = query(capture)
    actual = record['models']['m0_actual']
    assert result == {'status': 'available', 'model': 'm0_actual', 'home_mean': actual['home_rate'],
                      'away_mean': actual['away_rate'], 'source_available_at': now.isoformat(),
                      'capture_hash': record['hash'], 'artifact_hash': record['model_metadata']['bundle_sha256'],
                      'official_game_id': game['official_game_id'], 'kickoff_at': game['kickoff_at'],
                      'scope': 'full_game', 'source_evidence_hash': observer.digest(observer.canonical(record['input']['evidence'])),
                      'companions': {name: {'home_mean': record['models'][name]['home_rate'],
                                            'away_mean': record['models'][name]['away_rate']}
                                     for name in ('m0_projected', 'm1_projected')}}
    assert checks == ['archive']
    assert query(capture) == result and checks == ['archive']
    path.write_bytes(path.read_bytes())
    assert query(capture) == result and checks == ['archive', 'archive']


@pytest.mark.parametrize('field,value', [('league', 'MLB'), ('home_team_id', 'LG'),
                                        ('away_team_id', 'SS'), ('season', 2025),
                                        ('date', '2026-11-04'), ('official_game_id', 'other'),
                                        ('kickoff_at', '2026-11-05T12:01:00+00:00')])
def test_identity_mismatch_is_unavailable(capture, field, value):
    game = dict(capture[3]); game[field] = value
    assert query(capture, game)['status'] == 'unavailable'


def test_time_boundaries_and_future_capture(capture):
    now = capture[4]; kickoff = helpers.ledger.moment(capture[3]['kickoff_at'])
    assert query(capture, asof=now-timedelta(seconds=1), built=now)['status'] == 'unavailable'
    assert query(capture, asof=now+timedelta(seconds=1), built=now)['status'] == 'unavailable'
    assert query(capture, built=kickoff-timedelta(minutes=30))['status'] == 'unavailable'
    assert query(capture, built=kickoff-timedelta(minutes=30, seconds=1))['status'] == 'available'
    assert query(capture, asof=now.replace(tzinfo=None))['status'] == 'unavailable'


@pytest.mark.parametrize('tamper', ['rate', 'input', 'metadata', 'future', 'model', 'roster'])
def test_rehashed_semantic_tampering_is_unavailable(capture, tamper):
    _, path, original, _, now, _ = capture
    record = deepcopy(original)
    if tamper == 'rate':
        record['models']['m0_actual']['home_rate'] += 1
    elif tamper == 'input':
        record['input']['row']['home_id'] = 'LG'
    elif tamper == 'metadata':
        record['model_metadata']['bundle_sha256'] = '0'*64
    elif tamper == 'future':
        record['input']['evidence']['history']['received_at'] = (now+timedelta(seconds=1)).isoformat()
        record['input_sha256'] = observer.digest(observer.canonical(record['input']))
    elif tamper == 'model':
        record['models']['m0_actual'] = record['models']['m0_projected']
    else:
        record['input']['row']['roster']['home']['hitters'] = []
        record['input_sha256'] = observer.digest(observer.canonical(record['input']))
    rewrite(path, record)
    assert query(capture)['status'] == 'unavailable'


def test_broken_chain_and_size_cap_fail_closed(capture):
    _, path, _, _, _, _ = capture
    path.write_bytes(path.read_bytes().replace(b'"promoted":false', b'"promoted":true'))
    assert query(capture)['status'] == 'unavailable'
    with path.open('wb') as out:
        out.truncate(provider.MAX_BYTES+1)
    result = query(capture)
    assert result['status'] == 'unavailable' and '8 MiB' in result['reason']


def test_archive_failure_and_missing_file_are_unavailable(capture, monkeypatch):
    _, path, _, _, _, _ = capture
    def fail(*args):
        raise ValueError('archive changed')
    monkeypatch.setattr(observer, '_verify_archives', fail)
    assert query(capture)['status'] == 'unavailable'
    def source_fail(*args):
        raise provider.SourceError('bad official archive')
    monkeypatch.setattr(observer, '_verify_archives', source_fail)
    provider._cache.clear()
    assert query(capture)['status'] == 'unavailable'
    path.unlink()
    assert query(capture)['status'] == 'unavailable'


def test_forecast_from_different_candidate_is_not_used(capture):
    _, path, record, _, _, _ = capture
    record = deepcopy(record)
    record['models']['m0_actual']['home_rate'] = record['models']['m0_projected']['home_rate'] + 3
    record['models']['lam_baseline'] = {'home_rate': 99, 'away_rate': 99}
    rewrite(path, record)
    assert query(capture)['status'] == 'unavailable'


def test_tampered_companion_is_excluded_without_discarding_verified_m0(capture):
    _, path, original, _, _, _ = capture
    record = deepcopy(original)
    record['models']['m1_projected']['home_rate'] += 1
    rewrite(path, record)
    distribution = query(capture)
    assert distribution['status'] == 'available'
    assert set(distribution['companions']) == {'m0_projected'}


def test_priced_capture_verifies_market_archive(capture):
    root, path, _, game, now, checks = capture
    path.unlink()
    payload = helpers.payload('full_game_2way')
    payload['event'].update(home_id='SS', away_id='HT')
    payload['row'].update(home_id='SS', away_id='HT')
    payload['row']['history_coverage'] = {s: {'status': 'complete'} for s in ('home', 'away')}
    quote = helpers.quote(payload, root/'kbo-m0-forward-v1', now)
    payload['market'], payload['evidence']['odds'] = helpers.runner.price_packet(quote, root/'kbo-m0-forward-v1', payload['event'])
    recorder = observer.Recorder(path, helpers.ledger.validate_model_snapshot, clock=lambda: now)
    recorder.capture(payload)
    checks.clear()
    assert query(capture)['status'] == 'available'
    assert checks == ['archive', 'price']


def test_at_most_twenty_latest_verified_captures_are_cached(capture):
    _, path, first, _, now, checks = capture
    for i in range(1, 23):
        record = deepcopy(first)
        record.pop('hash'); record.pop('previous_hash')
        record['event_key'] = str(i)
        record['captured_at'] = (now+timedelta(seconds=i)).isoformat()
        observer._append(path, lambda rows, record=record: record)
    checks.clear()
    assert query(capture, asof=now+timedelta(seconds=22), built=now+timedelta(seconds=22))['status'] == 'available'
    assert len(checks) == 20
    assert query(capture, asof=now, built=now+timedelta(seconds=22))['status'] == 'unavailable'


def test_changed_model_file_fails_closed_even_with_cached_ledger(capture, monkeypatch, tmp_path):
    assert query(capture)['status'] == 'available'
    fake_bundle = tmp_path/'bundle.json'
    fake_bundle.write_bytes(provider.model.PATH.read_bytes()+b' ')
    monkeypatch.setattr(provider.model, 'PATH', fake_bundle)
    result = query(capture)
    assert result['status'] == 'unavailable' and 'artifact hash' in result['reason']


def test_kbo_league_is_bound_to_archived_schedule(tmp_path, monkeypatch):
    from tests.test_m1_service_source_binding import source_fixture
    payload, root, _ = source_fixture(tmp_path, monkeypatch)
    provider._verify_kbo_league(payload, root)
    wrapper = payload['evidence']['announcement']['body']
    bundle = json.loads(gzip.decompress((root/wrapper['relative_file']).read_bytes()))
    response = bundle['responses'][0]
    source = json.loads(base64.b64decode(response['body_base64']))
    source['game'][0]['LE_ID'] = '2'
    response['body_base64'] = base64.b64encode(observer.canonical(source)).decode('ascii')
    raw = observer.canonical(bundle); sha = observer.digest(raw)
    (root/'verified-bundles'/f'{sha}.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    wrapper['relative_file'] = f'verified-bundles/{sha}.json.gz'
    wrapper['archived_canonical_bundle_sha256'] = sha
    with pytest.raises(ValueError, match='league identity'):
        provider._verify_kbo_league(payload, root)


def test_index_preferred_over_oversized_ledger_and_cached(capture):
    _, ledger, record, _, _, checks = capture
    index, item, _ = write_index(capture)
    with ledger.open('wb') as out:
        out.truncate(provider.MAX_BYTES + 1)
    checks.clear()
    result = query(capture)
    assert result['status'] == 'available' and result['capture_hash'] == record['hash']
    assert checks == ['archive']
    assert query(capture) == result and checks == ['archive']
    item.write_bytes(item.read_bytes())
    assert query(capture) == result and checks == ['archive', 'archive']
    index.unlink()
    assert '8 MiB' in query(capture)['reason']


@pytest.mark.parametrize('failure', ['schema', 'path', 'hash', 'event', 'missing', 'count', 'duplicate', 'oversize'])
def test_bad_index_never_falls_back_to_valid_ledger(capture, failure):
    index, item, entry = write_index(capture)
    if failure == 'schema':
        put_index(index, [entry], 'wrong')
    elif failure == 'path':
        entry['file'] = '../predictions.jsonl'; put_index(index, [entry])
    elif failure == 'hash':
        item.write_bytes(item.read_bytes().replace(b'"promoted":false', b'"promoted":true'))
    elif failure == 'event':
        entry['event']['home_id'] = 'LG'; put_index(index, [entry])
    elif failure == 'missing':
        item.unlink()
    elif failure == 'count':
        put_index(index, [entry] * (provider.MAX_CAPTURES+1))
    elif failure == 'duplicate':
        put_index(index, [entry, entry])
    else:
        with index.open('wb') as out:
            out.truncate(provider.MAX_INDEX_BYTES + 1)
    assert query(capture)['status'] == 'unavailable'


def test_index_does_not_trust_rehashed_forecast(capture):
    record = deepcopy(capture[2])
    record['models']['m0_actual']['home_rate'] += 1
    record.pop('hash')
    record['hash'] = observer.digest(observer.canonical(record))
    write_index(capture, record)
    assert query(capture)['status'] == 'unavailable'


def test_index_record_must_be_canonical_and_within_cap(capture):
    _, item, _ = write_index(capture)
    item.write_text(json.dumps(capture[2], ensure_ascii=False, indent=2), encoding='utf8')
    assert query(capture)['status'] == 'unavailable'
    with item.open('wb') as out:
        out.truncate(provider.MAX_BYTES + 1)
    assert query(capture)['status'] == 'unavailable'


def test_index_empty_does_not_fallback(capture):
    index, _, _ = write_index(capture)
    put_index(index, [])
    assert query(capture)['status'] == 'unavailable'


def test_index_replays_source_without_claiming_complete_chain(capture):
    record = deepcopy(capture[2])
    record['previous_hash'] = 'a' * 64
    record.pop('hash')
    record['hash'] = observer.digest(observer.canonical(record))
    write_index(capture, record)
    result = query(capture)
    assert result['status'] == 'available' and result['capture_hash'] == record['hash']


def test_index_source_failure_never_falls_back(capture, monkeypatch):
    write_index(capture)
    monkeypatch.setattr(observer, '_verify_archives', lambda *args: (_ for _ in ()).throw(ValueError('bad source')))
    assert query(capture)['status'] == 'unavailable'
