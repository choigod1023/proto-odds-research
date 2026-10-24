import copy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pregame_research_capture as capture

NOW = datetime(2026, 10, 24, 3, tzinfo=timezone.utc)
START = '2026-10-24T10:00:00+00:00'


def document():
    return {'games': [{'league': 'KBO', 'home_team': 'LG', 'away_team': 'KT',
        'game_datetime': START, 'updated_at': '2026-10-24T02:00:00Z',
        'source': 'official_feed', 'source_url': 'https://example.test/fixture', 'game_id': 'g1',
        'starters': {'home': {'name': 'Pitcher', 'announced': True}},
        'lineups': {'home': [{'name': 'Batter', 'batting_order': 1}]},
        'benches': {'home': [{'name': 'Reserve'}]},
        'unavailable': {'away': [{'name': 'Out', 'status': 'suspended'}, {'name': 'Unknown'}]},
        'lineup_status': {'state': 'official_today'}}]}


def fixture():
    return {'event_id': 'evt1', 'sport': 'bs', 'league': 'KBO', 'home': 'LG', 'away': 'KT',
        'year': 2026, 'round': 122, 'date': '10.24(토) 19:00', 'status': '경기전',
        'prediction_revision_id': 'snap1',
        'decision_snapshot': {'decision_id': 'snap1', 'selection_id': 'sel1'}}


def record():
    return {'snapshot_id': 'snap1', 'event_id': 'evt1', 'as_of': '2026-10-24T01:00:00Z',
        'market_observed_at': '2026-10-24T01:00:00Z', 'captured_at': '2026-10-24T01:01:00Z',
        'kickoff': START, 'features': {'research_player_inputs': {'observed_at': '2026-10-24T00:58:00Z'}},
        'predictions': {'selection_id': 'sel1', 'odds': 1.8, 'probability': 0.6}}


def runtime(tmp_path, *, source=None, game=None, prediction=None):
    path = tmp_path / 'runtime.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE documents(name TEXT PRIMARY KEY,payload_json TEXT);
            CREATE TABLE artifacts(name TEXT PRIMARY KEY,payload_json TEXT);
            CREATE TABLE prediction_records(ledger_sequence INTEGER PRIMARY KEY,identity TEXT UNIQUE,
                event_id TEXT,snapshot_id TEXT,record_type TEXT,record_json TEXT);
            CREATE INDEX idx_prediction_event ON prediction_records(event_id,ledger_sequence);''')
        db.execute('INSERT INTO documents VALUES (?,?)', ('player_info', json.dumps(source or document())))
        db.execute('INSERT INTO artifacts VALUES (?,?)', ('picks_v2', json.dumps({'live': [game or fixture()]})))
        db.execute('INSERT INTO prediction_records VALUES (1,?,?,?,?,?)',
                   ('snap1', 'evt1', 'snap1', 'prediction', json.dumps(prediction or record())))
    return path


class Store:
    def __init__(self):
        self.evidence, self.pairs, self.results = [], [], []
    def append_evidence(self, **kwargs):
        self.evidence.append(kwargs)
        return {'id': len(self.evidence), 'datahash': 'a'*64}
    def append_pair(self, **kwargs):
        if not self.pairs:
            self.pairs.append({'id': 1, **kwargs})
        return self.pairs[-1]
    def append_result(self, **kwargs):
        self.results.append(kwargs)
        return kwargs
    def iter_pairs(self, *, after_id, limit):
        return iter([p for p in self.pairs if p['id'] > after_id][:limit])


def test_readonly_capture_typed_facts_and_exact_baseline(tmp_path):
    path = runtime(tmp_path)
    before = path.read_bytes()
    store = Store()
    summary = capture.capture_once(path, store, now=NOW)
    assert summary['captures_processed'] == 1
    assert path.read_bytes() == before
    facts = store.evidence[0]['typed_facts']
    assert {f['type'] for f in facts} == {'starters', 'lineups', 'benches', 'unavailable'}
    assert all(set(f) == {'type', 'subject', 'value'} for f in facts)
    assert not any('Unknown' in f['subject'] for f in facts)
    baseline = store.pairs[0]
    assert baseline['snapshot_id'] == 'snap1' and baseline['selection_id'] == 'sel1'
    assert baseline['odds'] == {'decimal': 1.8, 'observed_at': '2026-10-24T01:00:00+00:00'}
    assert baseline['baseline_decision']['feature_observed_at'] == '2026-10-24T00:58:00Z'
    assert baseline['baseline_decision']['frozen_at'] is None
    with capture.runtime_reader(path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM documents')


@pytest.mark.parametrize('change,counter', [
    ({'updated_at': '2026-10-24T04:00:00Z'}, 'future_source'),
    ({'updated_at': '2026-10-23T20:59:59Z'}, 'stale_source'),
    ({'updated_at': None}, 'missing_provenance'),
    ({'source': None}, 'missing_provenance'),
    ({'stale': True}, 'stale_source'),
    ({'game_datetime': '2026-10-24T11:00:00Z'}, 'missing_source'),
    ({'game_datetime': '2025-10-24T10:00:00Z'}, 'missing_source'),
    ({'game_datetime': '2026-10-24T10:00:00'}, 'missing_source'),
    ({'league': 'NPB'}, 'missing_source'),
])
def test_invalid_source_rejected(tmp_path, change, counter):
    source = document()
    source['games'][0].update(change)
    store = Store()
    summary = capture.capture_once(runtime(tmp_path, source=source), store, now=NOW)
    assert summary[counter] == 1
    assert not store.evidence and not store.pairs


def test_duplicates_and_t30(tmp_path):
    source = document()
    source['games'].append(copy.deepcopy(source['games'][0]))
    path = runtime(tmp_path, source=source)
    store = Store()
    assert capture.capture_once(path, store, now=NOW)['ambiguous_source'] == 1
    assert not store.pairs
    assert capture.capture_once(path, store,
        now=datetime.fromisoformat(START)-timedelta(minutes=30))['not_before_t30'] == 1


def test_season_roster_and_stale_starter_are_not_today_facts():
    row = document()['games'][0]
    row['lineup_status'] = {'state': 'season_roster'}
    row['starters']['home']['stale'] = True
    facts = capture.typed_facts(row)
    assert [f['kind'] for f in facts] == ['unavailable_player']


@pytest.mark.parametrize('change', [
    {'snapshot_id': 'other'}, {'event_id': 'other'},
    {'predictions': {'selection_id': 'other', 'odds': 1.8, 'probability': 0.6}},
    {'captured_at': '2026-10-24T04:00:00Z'},
    {'predictions': {'selection_id': 'sel1', 'odds': 1.8, 'probability': 99}},
])
def test_mismatched_baseline_rejected(tmp_path, change):
    prediction = record()
    prediction.update(change)
    store = Store()
    assert capture.capture_once(runtime(tmp_path, prediction=prediction), store, now=NOW)['missing_baseline'] == 1
    assert not store.pairs


def test_settlement_exact_snapshot_selection_and_official_source(tmp_path):
    path = runtime(tmp_path)
    store = Store()
    capture.capture_once(path, store, now=NOW)
    with sqlite3.connect(path) as db:
        for seq, snapshot, selection, source in [(2, 'other', 'sel1', 'proto_official'),
                (3, 'snap1', 'other', 'proto_official'), (4, 'snap1', 'sel1', 'tipster'),
                (5, 'snap1', 'sel1', 'proto_official')]:
            value = {'snapshot_id': snapshot, 'settled_at': '2026-10-24T12:00:00Z',
                     'captured_at': '2026-10-24T12:01:00Z', 'source': {'name': source},
                     'outcome': {'selection_id': selection, 'result': 'hit'}}
            db.execute('INSERT INTO prediction_records VALUES (?,?,?,?,?,?)',
                       (seq, str(seq), 'evt1', snapshot, 'settlement', json.dumps(value)))
    before = path.read_bytes()
    capture.capture_once(path, store, now=NOW+timedelta(hours=10))
    assert len(store.results) == 1
    assert store.results[0]['snapshot_id'] == 'snap1'
    assert store.results[0]['selection_id'] == 'sel1'
    assert path.read_bytes() == before


def test_optin_disabled_and_overlap_do_not_open_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv('PROODD_RESEARCH_DB_PATH', raising=False)
    assert capture.safe_capture() == {'status': 'disabled'}
    path = runtime(tmp_path)
    before = path.read_bytes()
    monkeypatch.setenv('PROODD_RESEARCH_DB_PATH', str(path))
    monkeypatch.setenv('PROODD_DB_PATH', str(path))
    assert capture.safe_capture()['reason'] == 'database_paths_overlap'
    assert path.read_bytes() == before


def test_real_store_repeated_capture_keeps_pair_immutable(tmp_path):
    store_module = pytest.importorskip('pregame_research_store')
    path = runtime(tmp_path)
    current = [NOW]
    store = store_module.PregameResearchStore(tmp_path / 'research.sqlite3',
        official_sources={'proto_official'}, clock=lambda: current[0])
    capture.capture_once(path, store, now=current[0])
    first = list(store.iter_pairs())
    current[0] += timedelta(minutes=1)
    capture.capture_once(path, store, now=current[0])
    assert list(store.iter_pairs()) == first


def test_evidence_survives_missing_or_stale_baseline(tmp_path):
    prediction = record()
    prediction['market_observed_at'] = '2026-10-23T23:00:00Z'
    store = Store()
    summary = capture.capture_once(runtime(tmp_path, prediction=prediction), store, now=NOW)
    assert summary['evidence_without_pair'] == 1
    assert len(store.evidence) == 1 and not store.pairs


def test_announced_lineup_and_availability_semantics():
    source = document()['games'][0]
    source['lineup_status'] = {'state': 'announced'}
    source['unavailable'] = {'home': [{'name': 'Absent', 'availability_status': 'out',
        'reason_code': 'not_in_roster', 'source_type': 'official_lineup'}]}
    facts = capture.typed_facts(source)
    assert any(f['kind'] == 'lineup_player' for f in facts)
    absence = next(f for f in facts if f['kind'] == 'unavailable_player')
    assert absence['reason_code'] == 'not_in_roster'
    assert absence['source_type'] == 'official_lineup'
    source['lineup_status']['stale'] = True
    assert not any(f['kind'] == 'lineup_player' for f in capture.typed_facts(source))


def test_stale_document_injuries_not_refreshed_by_game_update(tmp_path):
    source, game = document(), fixture()
    source['games'][0]['league'] = game['league'] = 'MLB'
    source['injuries_refreshed_at'] = '2026-10-22T00:00:00Z'
    source['games'][0]['unavailable'] = {'home': [{'name':'Injury','status':'out',
        'source_type':'official_roster'}]}
    store = Store()
    summary = capture.capture_once(runtime(tmp_path, source=source, game=game), store, now=NOW)
    assert summary['stale_or_missing_injury_source'] == 1
    assert not any(f['type'] == 'unavailable' for f in store.evidence[0]['typed_facts'])


def test_roster_requires_actual_per_player_observation_not_fresh_global_stamp():
    source = document()['games'][0]
    source['injuries_refreshed_at'] = NOW.isoformat()
    source['unavailable'] = {'home': [{'name':'Cached','status':'out',
        'source_type':'official_roster'}, {'name':'Fresh','status':'out',
        'source_type':'official_roster','observed_at':NOW.isoformat(),
        'source_url':'https://example.test/roster'}]}
    facts = capture.typed_facts(source, now=NOW)
    absences = [f for f in facts if f['kind']=='unavailable_player']
    assert [f['name'] for f in absences] == ['Fresh']
    assert absences[0]['observed_at'] == NOW.isoformat()


@pytest.mark.parametrize('table,name,limit', [('artifacts','picks_v2','ARTIFACT_BYTES'),
                                             ('documents','player_info','DOCUMENT_BYTES')])
def test_payload_guard_before_json_parse(tmp_path, monkeypatch, table, name, limit):
    path = runtime(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(f'UPDATE {table} SET payload_json=? WHERE name=?', ('not_json'*100, name))
    monkeypatch.setattr(capture, limit, 128)
    summary = capture.capture_once(path, Store(), now=NOW)
    assert summary['oversized_' + name] == 1  # Invalid JSON never parsed.


def test_real_store_new_lineup_same_baseline_and_two_providers(tmp_path):
    module = pytest.importorskip('pregame_research_store')
    path = runtime(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO documents VALUES (?,?)', ('kbo_pregame', json.dumps(document())))
    current = [NOW-timedelta(minutes=5)]
    store = module.PregameResearchStore(tmp_path/'research.sqlite3',
        official_sources={'proto_official'}, clock=lambda: current[0])
    capture.capture_once(path, store, now=current[0])
    first = list(store.iter_pairs())
    assert len(first) == 1 and len(first[0]['evidence_refs']) == 2
    source = document()
    source['games'][0]['lineups']['home'][0]['name'] = 'Replacement'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE documents SET payload_json=? WHERE name=?', (json.dumps(source),'player_info'))
    current[0] += timedelta(minutes=1)
    capture.capture_once(path, store, now=current[0])
    pairs = list(store.iter_pairs())
    assert len(pairs) == 2 and pairs[1]['revision'] == 2
    assert pairs[0]['baseline_decision'] == pairs[1]['baseline_decision']
    assert pairs[0]['snapshot_id'] == pairs[1]['snapshot_id'] == 'snap1'
    assert pairs[0]['evidence_refs'] != pairs[1]['evidence_refs']
    current[0] += timedelta(minutes=1)
    capture.capture_once(path, store, now=current[0])
    assert len(list(store.iter_pairs())) == 2


def test_latest_settlement_not_replayed_as_previous_revision(tmp_path):
    path = runtime(tmp_path)
    store = Store()
    capture.capture_once(path, store, now=NOW)
    with sqlite3.connect(path) as db:
        for seq, result in [(2,'hit'), (3,'miss')]:
            value = {'snapshot_id':'snap1','settled_at':'2026-10-24T12:00:00Z',
                'captured_at':'2026-10-24T12:01:00Z','source':{'name':'proto_official'},
                'outcome':{'selection_id':'sel1','result':result}}
            db.execute('INSERT INTO prediction_records VALUES (?,?,?,?,?,?)',
                (seq,str(seq),'evt1','snap1','settlement',json.dumps(value)))
    for _ in range(2):
        capture.capture_once(path, store, now=NOW+timedelta(hours=10))
    assert [r['outcome']['result'] for r in store.results] == ['miss','miss']


def test_wrapper_deadline_sanitized_and_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('PROODD_DB_PATH', str(tmp_path/'runtime'))
    monkeypatch.setenv('PROODD_RESEARCH_DB_PATH', str(tmp_path/'research'))
    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 12
        raise capture.subprocess.TimeoutExpired('secret', 12)
    monkeypatch.setattr(capture.subprocess,'run',timeout)
    assert capture.safe_capture() == {'status':'error','reason':'TimeoutExpired'}
    output = capsys.readouterr().out
    assert 'TimeoutExpired' in output and 'secret' not in output


def test_storage_budget_refuses_before_write(tmp_path, monkeypatch):
    path = tmp_path/'research'
    path.write_bytes(b'x'*32)
    store = Store()
    store.path = path
    monkeypatch.setattr(capture,'CAPTURE_BYTES',1024*1024+16)
    with pytest.raises(ValueError, match='research_size_limit'):
        capture._storage_guard(store)


def test_cursor_advances_across_bounded_runs_and_cycles(tmp_path, monkeypatch):
    path = runtime(tmp_path)
    store = Store()
    store.path = tmp_path/'research.sqlite3'
    store.pairs = [{'id':n,'event_id':f'event{n}','snapshot_id':f'snap{n}',
                    'selection_id':'sel1','kickoff':START} for n in (1,2)]
    monkeypatch.setattr(capture,'SETTLEMENT_PAIRS_PER_RUN',1)
    for expected in (1,0,1):
        with capture.runtime_reader(path) as db:
            capture.settle_pairs(db,store,NOW,capture.Counter())
        assert capture._cursor(store) == expected
    assert json.loads(Path(str(store.path)+'.cursor').read_text())['after_id'] == 1


def test_capture_failure_still_processes_existing_settlements(tmp_path, monkeypatch):
    path = runtime(tmp_path)
    store = Store()
    calls = []
    monkeypatch.setattr(capture,'settle_pairs',lambda *args: calls.append('settled'))
    def broken(*args):
        raise ValueError('malformed fixture')
    monkeypatch.setattr(capture,'collect_rows',broken)
    result = capture.capture_once(path,store,now=NOW)
    assert calls == ['settled'] and result['capture_stage_errors'] == 1


def test_capture_size_limit_reserves_room_for_results(tmp_path, monkeypatch):
    store = Store()
    store.path = tmp_path/'research.sqlite3'
    store.path.write_bytes(b'occupied')
    monkeypatch.setattr(capture,'CAPTURE_BYTES',1024*1024)
    counts = capture.Counter()
    assert capture._append(store,'evidence',counts,event_id='evt1') is None
    assert counts['capture_size_limit'] == 1
    assert capture._append(store,'result',counts,event_id='evt1') == {'event_id':'evt1'}
    monkeypatch.setattr(capture,'RESEARCH_BYTES',1024*1024)
    assert capture._append(store,'result',counts,event_id='evt2') is None
    assert counts['needs_archive'] == 1


def test_linux_memory_guard_does_not_spawn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('PROODD_DB_PATH',str(tmp_path/'runtime'))
    monkeypatch.setenv('PROODD_RESEARCH_DB_PATH',str(tmp_path/'research'))
    monkeypatch.setattr(capture.sys,'platform','linux')
    original = Path.read_text
    def meminfo(path,*args,**kwargs):
        return 'MemAvailable: 12345 kB\n' if str(path).replace('\\','/') == '/proc/meminfo' else original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',meminfo)
    monkeypatch.setattr(capture.subprocess,'run',lambda *a,**kw: pytest.fail('must not spawn'))
    result = capture.safe_capture()
    assert result['reason'] == 'memory_available_below_256mib'
    assert result['reason'] in capsys.readouterr().out


def test_missing_baseline_then_arrival_links_preserved_evidence(tmp_path):
    module = pytest.importorskip('pregame_research_store')
    path = runtime(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM prediction_records')
    current = [NOW-timedelta(minutes=5)]
    store = module.PregameResearchStore(tmp_path/'research.sqlite3',
        official_sources={'proto_official'},clock=lambda:current[0])
    assert capture.capture_once(path,store,now=current[0])['evidence_without_pair'] == 1
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO prediction_records VALUES (1,?,?,?,?,?)',
            ('snap1','evt1','snap1','prediction',json.dumps(record())))
    current[0] += timedelta(minutes=1)
    capture.capture_once(path,store,now=current[0])
    pairs = list(store.iter_pairs())
    assert len(pairs) == 1 and pairs[0]['evidence_refs'][0]['id'] == 1


def test_synthetic_near_limit_document_subprocess_peak(tmp_path):
    source = document()
    source['unused_padding'] = 'x'*(capture.DOCUMENT_BYTES-4096)
    path = runtime(tmp_path,source=source)
    script = '''
import sys,json,os
from datetime import datetime
import pregame_research_capture as c
counts=c.Counter()
with c.runtime_reader(sys.argv[1]) as db:
    rows=list(c.collect_rows(db,datetime.fromisoformat('2026-10-24T03:00:00+00:00'),counts))
if os.name=='nt':
    import ctypes
    from ctypes import wintypes
    class PMC(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in
            ['peak','working','qpp','qp','qpnp','qnp','page','peakpage']]
    p=PMC();p.cb=ctypes.sizeof(p)
    ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(PMC),wintypes.DWORD]
    assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(p),p.cb)
    peak=p.peak
else:
    import resource
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
print(json.dumps({'rows':len(rows),'peak_bytes':peak,'counters':dict(counts)}))
'''
    result = capture.subprocess.run([sys.executable,'-c',script,str(path)],
        cwd=Path(capture.__file__).parent,capture_output=True,text=True,timeout=12)
    assert result.returncode == 0, result.stderr
    measurement = json.loads(result.stdout)
    assert measurement['rows'] == 1 and measurement['peak_bytes'] > 0
    print('synthetic_16MiB_document_child_peak=' + json.dumps(measurement))
