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
    later = dict(capture, captured_at='2026-11-06T08:01:00+00:00')
    later['hash'] = b.digest(b.canonical({k: v for k, v in later.items() if k != 'hash'}))
    assert b.match_operating(later, export_data, NOW.isoformat())['reason'] == 'm0_after_operating_as_of'
    import copy
    withheld = copy.deepcopy(op)
    withheld.update(snapshot_id='later', decision_id='later', ledger_sequence=2,
                    as_of='2026-11-06T08:05:00+00:00', captured_at='2026-11-06T08:05:00+00:00')
    withheld['predictions'].update(action='withhold', selection_id=None, offer_id=None)
    withheld['features']['tipster_hybrid'].update(status='withheld')
    withheld['record_hash'] = b._hash_row(withheld)
    result = b.match_operating(capture, make_export([op, withheld]), NOW.isoformat())
    assert result['status'] == 'excluded' and 'operating_revision_excluded:' in result['reason']




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
    with pytest.raises(ValueError, match='discovery_no_match'):
        run_export(path, target={**target, 'market_id': 'G101:260131:45'})
    with pytest.raises(ValueError, match='discovery_no_match'):
        run_export(path, target={**target, 'home_id': 'LG'})



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
