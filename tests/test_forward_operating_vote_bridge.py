import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.kbo_forward import operating_vote_bridge as b
from scripts.export_forward_operating_votes import export

NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)


def row(seq=1):
    r = {'record_type': 'prediction', 'event_id': 'e', 'snapshot_id': 's'+str(seq),
         'ledger_sequence': seq, 'captured_at': '2026-11-06T08:00:00+00:00', 'kickoff': '2026-11-06T09:30:00+00:00'}
    r['record_hash'] = b._hash_row(r)
    return r


def db_file(tmp_path, rows):
    path = tmp_path / 'local.sqlite'
    c = sqlite3.connect(path)
    c.execute('create table prediction_records (event_id text, record_type text, ledger_sequence integer, record_json text)')
    c.executemany('insert into prediction_records values (?,?,?,?)', [(r['event_id'], r['record_type'], r['ledger_sequence'], json.dumps(r)) for r in rows])
    c.commit(); c.close()
    return path


def test_export_readonly_counts_and_bounded(tmp_path):
    path = db_file(tmp_path, [row(1), row(2)])
    before = path.read_bytes()
    out = export(path, ['e'])
    assert out['metadata']['events']['e'] == {'count': 2, 'max_sequence': 2}
    assert len(out['records']) == 2 and path.read_bytes() == before
    with pytest.raises(ValueError, match='duplicates'):
        export(path, ['e', 'e'])
    with pytest.raises(sqlite3.OperationalError):
        export(tmp_path / 'absent.db', ['e'])
    assert not (tmp_path / 'absent.db').exists()


def test_completeness_requires_post_cutoff_complete_rows_and_receipt(tmp_path):
    out = export(db_file(tmp_path, [row(1), row(2)]), ['e'])
    query = datetime(2026, 11, 7, tzinfo=timezone.utc)
    out['metadata']['queried_at'] = query.isoformat()
    assert b._export_completeness(out['records'], out['metadata'], query.isoformat(), query)[0]
    assert not b._export_completeness(out['records'][:1], out['metadata'], query.isoformat(), query)[0]
    assert not b._export_completeness(out['records'], out['metadata'], None, query)[0]
    out['metadata']['queried_at'] = '2026-11-06T08:00:00+00:00'
    assert not b._export_completeness(out['records'], out['metadata'], query.isoformat(), query)[0]


def test_offline_real_export_does_not_claim_complete_or_authentication():
    path = Path('experiments/forward-vote-bridge-20261104/operating-export.json.gz')
    if not path.exists(): pytest.skip('local read-only probe not available')
    x = b.load_operating_export({'export_path': path}, ['20261106KTSS0'], NOW.isoformat())
    assert len(x['records']) == 21 and x['complete'] is False
    assert all(not (r['features']['fixture'].get('source_event_id') or r['features']['fixture'].get('match_id')) for r in x['records'])
    assert all(r['record_hash'] == b._hash_row(r) for r in x['records'])
    assert b.match_operating({}, x, NOW.isoformat())['status'] == 'excluded'


def test_offer_identity_checks_exact_round_number_and_both_types():
    option = {'selection_id': 'sel_1'}
    r = {'features': {'fixture': {'round': 131}}}
    def offer(number):
        raw = '|'.join(json.dumps(x, ensure_ascii=False, separators=(',', ':')) for x in ['sel_1', 131, number])
        return 'off_' + hashlib.sha256(raw.encode()).hexdigest()[:16]
    option['offer_id'] = offer('44')
    assert b._offer_matches(r, option, 'G101:260131:44')
    assert not b._offer_matches(r, option, 'G101:260131:45')
    assert not b._offer_matches(r, option, 'G101:260132:44')
    option['offer_id'] = offer(44)
    assert b._offer_matches(r, option, 'G101:260131:44')


def test_bad_m0_and_oversized_input_fail_closed():
    assert b.match_operating({}, {'records': []}, NOW.isoformat())['reason'] == 'not_m1_priced_capture'
    result = b.match_operating({}, {'records': [None] * 1001}, NOW.isoformat())
    assert result['status'] == 'excluded' and result['reason'] == 'export_record_limit_or_shape'




def test_shadow_uses_saved_operating_price_and_blocks_late_or_withheld(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('op_fixture', 'tests/test_operating_ledger_inputs.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    op, _ = mod.operating_record()
    op['features']['fixture'].update(league='KBO', source_event_id='g1', round=131, home='삼성', away='KT', year=2026, date='11.06(금) 18:30')
    op['kickoff'] = '2026-11-06T09:30:00+00:00'
    op['as_of'] = op['captured_at'] = op['market_observed_at'] = '2026-11-06T08:00:00+00:00'
    op['schema_version'] = 'pregame-prediction-ledger-v1'
    # Synthetic IDs use the real production offer hash recipe.
    for o in op['features']['options']:
        raw = '|'.join(json.dumps(x, ensure_ascii=False, separators=(',', ':'))
                       for x in [o['selection_id'], 131, '44'])
        o['offer_id'] = 'off_' + hashlib.sha256(raw.encode()).hexdigest()[:16]
    selected = next(o for o in op['features']['options'] if o['selection_id'] == op['predictions']['selection_id'])
    op['predictions']['offer_id'] = selected['offer_id']
    op['record_hash'] = b._hash_row(op)
    meta = {'all_coefficients_preserved_by': '2026-01-01T00:00:00+00:00'}
    models = {'m0_actual': {'probabilities_2way': {'home': .6, 'away': .4}}}
    payload = {'event': {'official_game_id': 'g1', 'season': 2026, 'home_id': 'SS', 'away_id': 'KT', 'kickoff_at': op['kickoff']},
               'market': {'kind': 'full_game_2way', 'settlement_rule': 'draw_void_refund',
                          'market_id': 'G101:260131:44', 'odds': {'home': 1.8, 'away': 2.0}},
               'evidence': {'odds': {'received_at': '2026-11-06T07:50:00+00:00'}}, 'row': {}}
    capture = {'schema': 'm1-pregame-rehearsal-v1', 'type': 'capture', 'input': payload,
               'input_sha256': b.digest(b.canonical(payload)), 'model_metadata': meta,
               'captured_at': '2026-11-06T07:51:00+00:00', 'models': models,
               'choices': {'m0_actual': 'home'}, 'previous_hash': '0'*64}
    capture['hash'] = b.digest(b.canonical(capture))
    monkeypatch.setattr(b, 'metadata', lambda: meta)
    monkeypatch.setattr(b, 'forecast', lambda row: models)
    monkeypatch.setattr(b, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(b, '_verify_market_bytes', lambda *a: None)
    def make_export(rows):
        return {'records': rows, 'evidence': {}, 'export_sha256': 'a'*64,
                'source_receipt': {'transport': 'flyctl_ssh', 'export_sha256': 'a'*64, 'received_at': '2026-11-06T08:10:00+00:00'},
                'metadata': {'schema': 'operating-event-export-v1', 'query_mode': 'sqlite_readonly_transaction',
                             'exporter_sha256': hashlib.sha256(Path('scripts/export_forward_operating_votes.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                             'exporter_raw_sha256': hashlib.sha256(Path('scripts/export_forward_operating_votes.py').read_bytes()).hexdigest(),
                             'queried_at': '2026-11-06T08:09:00+00:00',
                             'events': {op['event_id']: {'count': len(rows), 'max_sequence': max(r['ledger_sequence'] for r in rows)}}}}
    export_data = make_export([op])
    got = b.match_operating(capture, export_data, NOW.isoformat())
    assert got['status'] == 'eligible', got
    assert got['shared_prices'] == {'home': 1.49, 'away': 2.1}
    assert got['original_m0_market']['odds'] == {'home': 1.8, 'away': 2.0}
    assert got['three_selections']['m0_actual'] == 'home'
    assert got['comparison_kind'] == 'operating_price_shadow'
    assert not got['official_raw_price_verified'] and not got['formal_promotion_eligible']
    assert got['three_selections']['market'] == 'home'
    assert got['market_probability_source'] == 'normalized_inverse_shared_odds'
    import copy
    reversed_saved = copy.deepcopy(op)
    for option in reversed_saved['features']['options']:
        if option['market'] == '승패':
            value = .2 if option['selection'] == '홈' else .8
            option['market_probability'] = option['final_probability'] = value
    reversed_saved['predictions']['probability'] = .8
    reversed_saved['predictions']['probability_detail']['market'] = .8
    reversed_saved['predictions']['probability_detail']['final'] = .8
    reversed_saved['record_hash'] = b._hash_row(reversed_saved)
    reversed_result = b.match_operating(capture, make_export([reversed_saved]), NOW.isoformat())
    assert reversed_result['status'] == 'eligible', reversed_result
    assert reversed_result['three_selections']['market'] == 'home'
    assert reversed_result['market_probabilities']['home'] > .5
    tied = copy.deepcopy(op)
    for option in tied['features']['options']:
        if option['market'] == '승패': option['odds'] = 1.49
    tied['predictions']['odds'] = 1.49
    tied['record_hash'] = b._hash_row(tied)
    assert b.match_operating(capture, make_export([tied]), NOW.isoformat())['reason'] == 'market_selection_tied'
    later = dict(capture, captured_at='2026-11-06T08:01:00+00:00')
    later['hash'] = b.digest(b.canonical({k: v for k, v in later.items() if k != 'hash'}))
    assert b.match_operating(later, export_data, NOW.isoformat())['reason'] == 'm0_after_operating_as_of'
    withheld = copy.deepcopy(op)
    withheld.update(snapshot_id='later', decision_id='later', ledger_sequence=2,
                    as_of='2026-11-06T08:05:00+00:00', captured_at='2026-11-06T08:05:00+00:00')
    withheld['predictions'].update(action='withhold', selection_id=None, offer_id=None)
    withheld['features']['tipster_hybrid'].update(status='withheld')
    withheld['record_hash'] = b._hash_row(withheld)
    result = b.match_operating(capture, make_export([op, withheld]), NOW.isoformat())
    assert result['status'] == 'excluded' and 'operating_revision_excluded:' in result['reason']
    degraded = copy.deepcopy(op)
    degraded['features']['fixture']['source_event_id'] = None
    for option in degraded['features']['options']:
        option['offer_id'] = 'unresolved-' + option['selection_id']
    chosen = next(o for o in degraded['features']['options']
                  if o['selection_id'] == degraded['predictions']['selection_id'])
    degraded['predictions']['offer_id'] = chosen['offer_id']
    degraded['record_hash'] = b._hash_row(degraded)
    candidate = make_export([degraded])
    assert b.match_operating(capture, candidate, NOW.isoformat())['reason'] == 'operating_game_number_unverified'
    candidate['metadata']['mapping'] = {
        'mapping': 'degraded_unique_fixture_round_two_way', 'candidate_event_count': 1,
        'uniqueness_scope': 'last_3_days_bounded_prediction_rows', 'game_number_verified': False,
        'internal_event_id': degraded['event_id'], 'official_id': 'g1', 'home_id': 'SS',
        'away_id': 'KT', 'kickoff': op['kickoff'], 'market_id': 'G101:260131:44'}
    result = b.match_operating(capture, candidate, NOW.isoformat())
    assert result['status'] == 'eligible' and result['comparison_kind'] == 'degraded_first_shadow'
    assert result['market_id'] is None and result['candidate_m0_market_id'] == 'G101:260131:44'
    assert result['identity_limitation'] == 'offer_id_recipe_or_game_number_unverified'
    assert not result['game_number_verified'] and not result['formal_promotion_eligible']
    assert not result['official_raw_price_verified'] and not result['final_operating_performance_verified']
    candidate['metadata']['mapping']['candidate_event_count'] = 2
    assert b.match_operating(capture, candidate, NOW.isoformat())['reason'] == 'operating_game_number_unverified'




def test_official_discovery_resolves_internal_id_only_after_exact_offer_match(tmp_path):
    from scripts.export_forward_operating_votes import export as run_export
    import importlib.util
    spec = importlib.util.spec_from_file_location('op_discovery_fixture', 'tests/test_operating_ledger_inputs.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    op, _ = mod.operating_record()
    now = datetime.now(timezone.utc)
    kickoff = (now + timedelta(days=1)).replace(second=0, microsecond=0)
    local = kickoff.astimezone(timezone(timedelta(hours=9)))
    op['features']['fixture'].update(league='KBO', sport='bs', home='삼성', away='KT',
                                     year=local.year, date=local.strftime('%m.%d(금) %H:%M'),
                                     source_event_id=None, match_id=None, round=131)
    op['kickoff'] = kickoff.isoformat(); op['captured_at'] = now.isoformat()
    for o in op['features']['options']:
        raw = '|'.join(json.dumps(x, ensure_ascii=False, separators=(',', ':')) for x in [o['selection_id'], 131, '44'])
        o['offer_id'] = 'off_' + hashlib.sha256(raw.encode()).hexdigest()[:16]
    op['record_hash'] = b._hash_row(op)
    path = tmp_path/'discover.db'; c = sqlite3.connect(path)
    c.execute('create table prediction_records(event_id text, record_type text, ledger_sequence integer, captured_at text, record_json text)')
    c.execute('insert into prediction_records values(?,?,?,?,?)', (op['event_id'], 'prediction', 1, op['captured_at'], json.dumps(op)))
    c.commit(); c.close()
    target = {'official_id': 'g1', 'season': local.year, 'home_id': 'SS', 'away_id': 'KT',
              'kickoff': kickoff.isoformat(), 'market_id': 'G101:260131:44'}
    got = run_export(path, target=target)
    assert got['metadata']['mapping']['internal_event_id'] == op['event_id']
    assert got['metadata']['mapping']['official_id'] == 'g1'
    assert list(got['metadata']['events']) == [op['event_id']]
    with pytest.raises(ValueError, match='discovery_offer_id_unverified'):
        run_export(path, target={**target, 'market_id': 'G101:260131:45'})
    with pytest.raises(ValueError, match='discovery_no_match'):
        run_export(path, target={**target, 'home_id': 'LG'})
    # A later revision with changed IDs must not inherit the older strict match.
    import copy
    newer = copy.deepcopy(op)
    newer['ledger_sequence'] = 2
    newer['as_of'] = now.isoformat()
    for option in newer['features']['options']:
        option['offer_id'] = 'different-' + option['selection_id']
    c = sqlite3.connect(path)
    c.execute('insert into prediction_records values(?,?,?,?,?)',
              (newer['event_id'], 'prediction', 2, newer['captured_at'], json.dumps(newer)))
    c.commit(); c.close()
    with pytest.raises(ValueError, match='discovery_offer_id_unverified'):
        run_export(path, target=target)
    assert run_export(path, target=target, allow_degraded=True)['metadata']['mapping']['mapping'] == 'degraded_unique_fixture_round_two_way'



def test_loader_completeness_requires_receipt_bound_to_export_bytes(tmp_path):
    result = export(db_file(tmp_path, [row(1), row(2)]), ['e'])
    query = datetime(2026, 11, 7, tzinfo=timezone.utc)
    result['metadata']['queried_at'] = query.isoformat()
    path = tmp_path/'export.json'
    path.write_text(json.dumps(result), encoding='utf8')
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {'transport': 'flyctl_ssh', 'export_sha256': file_hash, 'received_at': query.isoformat()}
    state = {'export_path': path, 'source_receipt': receipt}
    assert b.load_operating_export(state, ['g1'], query.isoformat())['complete'] is True
    receipt['export_sha256'] = 'wrong'
    assert b.load_operating_export(state, ['g1'], query.isoformat())['complete'] is False



def test_degraded_discovery_requires_unique_event_exact_time_round_and_market(tmp_path):
    from scripts.export_forward_operating_votes import export as run_export
    import importlib.util
    spec = importlib.util.spec_from_file_location('op_degraded_fixture', 'tests/test_operating_ledger_inputs.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    op, _ = mod.operating_record()
    now = datetime.now(timezone.utc)
    kickoff = (now + timedelta(days=1)).replace(second=0, microsecond=0)
    local = kickoff.astimezone(timezone(timedelta(hours=9)))
    op['features']['fixture'].update(league='KBO', sport='bs', home='삼성', away='KT',
                                     year=local.year, date=local.strftime('%m.%d(금) %H:%M'),
                                     source_event_id=None, match_id=None, round=131)
    op['kickoff'] = kickoff.isoformat(); op['captured_at'] = now.isoformat()
    path = tmp_path/'discover.db'; c = sqlite3.connect(path)
    c.execute('create table prediction_records(event_id text, record_type text, ledger_sequence integer, captured_at text, record_json text)')
    def insert(r):
        c.execute('insert into prediction_records values(?,?,?,?,?)', (r['event_id'], 'prediction', r['ledger_sequence'], r['captured_at'], json.dumps(r)))
    insert(op); c.commit()
    target = {'official_id': 'g1', 'season': local.year, 'home_id': 'SS', 'away_id': 'KT',
              'kickoff': kickoff.isoformat(), 'market_id': f'G101:{local.year % 100:02d}0131:44'}
    with pytest.raises(ValueError, match='discovery_offer_id_unverified'):
        run_export(path, target=target)
    got = run_export(path, target=target, allow_degraded=True)
    mapping = got['metadata']['mapping']
    assert mapping['mapping'] == 'degraded_unique_fixture_round_two_way'
    assert not mapping['game_number_verified'] and mapping['candidate_event_count'] == 1
    assert mapping['uniqueness_scope'] == 'last_3_days_bounded_prediction_rows'
    with pytest.raises(ValueError, match='discovery_no_match'):
        run_export(path, target={**target, 'kickoff': (kickoff+timedelta(minutes=1)).isoformat()}, allow_degraded=True)
    with pytest.raises(ValueError, match='discovery_no_match'):
        run_export(path, target={**target, 'market_id': f'G101:{local.year % 100:02d}0132:44'}, allow_degraded=True)
    import copy
    ambiguous_roster = copy.deepcopy(op)
    ambiguous_roster['features']['options'].append(copy.deepcopy(ambiguous_roster['features']['options'][0]))
    c.execute('update prediction_records set record_json=? where ledger_sequence=1', (json.dumps(ambiguous_roster),))
    c.commit()
    with pytest.raises(ValueError, match='discovery_complete_two_way_missing'):
        run_export(path, target=target, allow_degraded=True)
    c.execute('update prediction_records set record_json=? where ledger_sequence=1', (json.dumps(op),))
    c.commit()
    duplicate = copy.deepcopy(op)
    duplicate['event_id'] = 'another-event'; duplicate['ledger_sequence'] = 2
    duplicate['features']['options'] = []  # An incomplete competing event still defeats uniqueness.
    insert(duplicate); c.commit(); c.close()
    with pytest.raises(ValueError, match='discovery_ambiguous'):
        run_export(path, target=target, allow_degraded=True)


def test_refresh_transport_replay_and_idle(tmp_path, monkeypatch):
    import io
    from datetime import datetime
    now = datetime(2026, 11, 6, 8, 9, tzinfo=timezone.utc)
    event = {'official_game_id': 'g1', 'season': 2026, 'home_id': 'SS', 'away_id': 'KT',
             'kickoff_at': '2026-11-06T18:30:00+09:00', 'market_id': 'G101:260131:44'}
    script = Path('scripts/export_forward_operating_votes.py').read_bytes()
    one = row(1)
    result = {'metadata': {'schema': 'operating-event-export-v1', 'query_mode': 'sqlite_readonly_transaction',
              'exporter_sha256': hashlib.sha256(script.replace(b'\r\n', b'\n')).hexdigest(),
              'exporter_raw_sha256': hashlib.sha256(script).hexdigest(),
              'queried_at': now.isoformat(), 'events': {'e': {'count': 1, 'max_sequence': 1}},
              'mapping': {'official_id': 'g1', 'home_id': 'SS', 'away_id': 'KT',
                          'kickoff': event['kickoff_at'], 'market_id': event['market_id'], 'internal_event_id': 'e'}},
              'records': [one], 'evidence': {}}
    raw = json.dumps(result, ensure_ascii=False).encode()
    observed = []
    class Fake:
        def __init__(self, args, **kwargs):
            observed.append((args, kwargs)); self.stdout = io.BytesIO(raw); self.stderr = io.BytesIO(b'')
        def wait(self, timeout=None): return 0
        def kill(self): pass
    monkeypatch.setattr(b.subprocess, 'Popen', Fake)
    state = {'export_path': tmp_path/'state'/'operating-export.json', 'm0_archive_root': 'model',
             'm0_price_archive_root': 'prices', 'price_archive_root': 'op-prices'}
    assert b.refresh_operating_export(state, [], now)['exports_by_official_id'] == {}
    assert observed == [] and not (tmp_path/'state').exists()
    got = b.refresh_operating_export(state, [event], now, clock=lambda: now)
    assert observed[0][1]['stdin'] is b.subprocess.DEVNULL
    assert '--pty=false' in observed[0][0] and '-q' in observed[0][0]
    assert Path(got['export_path']).read_bytes() == raw
    assert hashlib.sha256(raw).hexdigest() == got['export_sha256']
    receipt = json.loads(Path(got['source_receipt_path']).read_bytes())
    assert receipt == got['source_receipt'] and receipt['exit_code'] == 0
    replay = b.load_operating_export({**state, 'export_path': got['export_path'], 'source_receipt': receipt}, ['g1'], now.isoformat())
    assert replay['records'] == [one] and replay['export_sha256'] == got['export_sha256']
    assert got['m0_archive_root'] == 'model' and got['m0_price_archive_root'] == 'prices'
    # Content-addressed replay is idempotent and never creates a mutable latest file.
    again = b.refresh_operating_export(state, [event], now, clock=lambda: now)
    assert got['export_path'] == again['export_path'] and not state['export_path'].exists()
    result['records'][0]['record_hash'] = 'tampered'
    raw = json.dumps(result, ensure_ascii=False).encode()
    with pytest.raises(ValueError, match='remote_export_rows_mismatch'):
        b.refresh_operating_export(state, [event], now, clock=lambda: now)


def test_transport_rejects_nonzero_timeout_and_output_overflow(monkeypatch):
    import io
    import subprocess
    class Fake:
        rc = 1; out = b'valid looking json'; err = b'Error: The handle is invalid.'; timeout = False
        def __init__(self, *args, **kwargs): self.stdout = io.BytesIO(self.out); self.stderr = io.BytesIO(self.err)
        def wait(self, timeout=None):
            if self.timeout and timeout is not None: raise subprocess.TimeoutExpired('flyctl', timeout)
            return self.rc
        def kill(self): pass
    monkeypatch.setattr(b.subprocess, 'Popen', Fake)
    with pytest.raises(ValueError, match='nonzero_exit:1'):
        b._bounded_fly('safe-command')
    Fake.rc = 0; Fake.timeout = True
    with pytest.raises(ValueError, match='transport_timeout'):
        b._bounded_fly('safe-command')
    Fake.timeout = False; Fake.out = b'x' * 65; Fake.err = b''
    monkeypatch.setattr(b, 'MAX_EXPORT_BYTES', 64)
    with pytest.raises(ValueError, match='stdout_limit_exceeded'):
        b._bounded_fly('safe-command')


def test_refresh_checks_script_and_refuses_bad_event(tmp_path, monkeypatch):
    now = datetime(2026, 11, 6, 8, 9, tzinfo=timezone.utc)
    event = {'official_game_id': 'g1', 'season': 2026, 'home_id': 'SS', 'away_id': 'KT',
             'kickoff_at': '2026-11-06T18:30:00+09:00', 'market_id': 'G101:260131:44'}
    state = {'export_path': tmp_path/'export.json'}
    with pytest.raises(ValueError, match='refresh_event_identity_invalid'):
        b.refresh_operating_export(state, [{**event, 'market_id': 'x;touch'}], now, clock=lambda: now)
    with pytest.raises(ValueError, match='refresh_event_limit_exceeded'):
        b.refresh_operating_export(state, [event]*6, now, clock=lambda: now)
    monkeypatch.setattr(b, '_bounded_fly', lambda command: json.dumps({'metadata': {'exporter_raw_sha256': 'bad'}}).encode())
    with pytest.raises(ValueError, match='remote_exporter_hash_or_clock_mismatch'):
        b.refresh_operating_export(state, [event], now, clock=lambda: now)
    assert not (tmp_path/'operating-export-archive').exists()


def test_bootstrap_packages_standalone_exporter():
    from research.kbo_forward.bootstrap_runtime import PREFIXES
    assert 'scripts/export_forward_operating_votes.py' in PREFIXES


def test_real_export_offers_match_observed_operating_snapshot_game_number():
    path = Path('experiments/forward-vote-bridge-20261104/operating-export.json.gz')
    if not path.exists(): pytest.skip('read-only probe not present')
    records = json.loads(gzip.decompress(path.read_bytes()))['records']
    record = next(r for r in records if any(o.get('offer_id') == 'off_4538025be5e96f8f' for o in r['features']['options']))
    options = [o for o in record['features']['options'] if o.get('market') == '승패']
    # 2395 is the actual separately observed same-fixture snapshot game_no;
    # this is not proof of a Betman receipt or an actual M0 capture.
    assert len(options) == 2
    assert all(b._offer_matches(record, option, 'G101:260131:2395') for option in options)
    assert not any(b._offer_matches(record, option, 'G101:260131:2396') for option in options)


def test_refresh_multiple_events_isolates_partial_failures(tmp_path, monkeypatch):
    now = datetime(2026, 11, 6, 8, 9, tzinfo=timezone.utc)
    event = {'official_game_id': 'g1', 'season': 2026, 'home_id': 'SS', 'away_id': 'KT',
             'kickoff_at': '2026-11-06T18:30:00+09:00', 'market_id': 'G101:260131:44'}
    script = Path('scripts/export_forward_operating_votes.py').read_bytes(); one = row(1)
    result = {'metadata': {'schema': 'operating-event-export-v1', 'query_mode': 'sqlite_readonly_transaction',
              'exporter_sha256': hashlib.sha256(script.replace(b'\r\n', b'\n')).hexdigest(),
              'exporter_raw_sha256': hashlib.sha256(script).hexdigest(),
              'queried_at': now.isoformat(), 'events': {'e': {'count': 1, 'max_sequence': 1}},
              'mapping': {'official_id': 'g1', 'home_id': 'SS', 'away_id': 'KT',
                          'kickoff': event['kickoff_at'], 'market_id': event['market_id'], 'internal_event_id': 'e'}},
              'records': [one], 'evidence': {}}
    raw = json.dumps(result).encode()
    def transport(command):
        if '--official-id g2 ' in command: raise ValueError('fly_transport_nonzero_exit:1')
        return raw
    monkeypatch.setattr(b, '_bounded_fly', transport)
    got = b.refresh_operating_export({'export_path': tmp_path/'export.json'},
                                     [event, {**event, 'official_game_id': 'g2'}], now, clock=lambda: now)
    assert set(got['exports_by_official_id']) == {'g1'}
    assert got['errors_by_official_id'] == {'g2': 'fly_transport_nonzero_exit:1'}
    assert Path(got['exports_by_official_id']['g1']['source_receipt_path']).is_file()
