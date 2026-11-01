"""Bounded local refresh memory benchmark; no input database or network access.

Creates synthetic SQLite fixtures from repository picks, runs isolated children and
compares exact stored outputs. Measurements are process peaks, not production RSS.
"""
import copy
from contextlib import closing
import ctypes
import hashlib
import gzip
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import sqlite3
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def memory():
    if sys.platform == 'win32':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ('peak', 'rss', 'qpp', 'qp', 'qnp', 'qn', 'page', 'peakpage')]
        c = Counters(); c.cb = ctypes.sizeof(c)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True); kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'rss_kib': c.rss // 1024, 'peak_kib': c.peak // 1024}
    values = dict(x.split(':', 1) for x in Path('/proc/self/status').read_text().splitlines() if ':' in x)
    return {'rss_kib': int(values['VmRSS'].split()[0]), 'peak_kib': int(values['VmHWM'].split()[0])}


def create_fixture(path, fixture, live_count=2807, past_count=162, prediction_count=7780, settlement_count=1241, history_count=1000):
    from runtime_db import RuntimeDatabase
    from prediction_ledger import SCHEMA_VERSION, _sha256
    from prediction_runtime import ledger_features
    original = fixture.read_bytes(); seed = json.loads(original)
    games = seed.get('live', []) + seed.get('past', [])
    if not games or prediction_count < live_count + past_count or settlement_count > live_count + past_count:
        raise ValueError('invalid fixture sizes')
    total = live_count + past_count
    doc = {k: copy.deepcopy(v) for k, v in seed.items() if k not in ('live', 'past')}
    synthetic = []
    for i in range(total):
        game = copy.deepcopy(games[i % len(games)])
        game['home'] = f"{game.get('home', '')} 합성{i} 🏟️"
        game['event_id'] = f'synthetic-event-{i:05d}'
        game['synthetic_fixture'] = '합성 데이터 🏟️'
        synthetic.append(game)
    doc['live'], doc['past'] = synthetic[:live_count], synthetic[live_count:]
    db = RuntimeDatabase(path); db.store_artifact('picks_v2', doc)
    records = []; previous = None; latest = {}
    for i in range(prediction_count):
        event = f'synthetic-event-{i % total:05d}'; snapshot = f'synthetic-prediction-{i:06d}'; latest[event] = snapshot
        record = dict(schema_version=SCHEMA_VERSION, record_type='prediction', snapshot_id=snapshot,
                      decision_id=snapshot, event_id=event, as_of='2026-01-01T00:00:00Z',
                      kickoff='2026-01-02T00:00:00Z', market_observed_at='2026-01-01T00:00:00Z',
                      captured_at='2026-01-01T00:00:00Z', model={'synthetic': True},
                      features=ledger_features(synthetic[i % total]), versions={'decision_schema':'synthetic','operating_model':None,'residual_model':None},
                      input_revision_hash=None, decision_snapshot_hash=_sha256({'synthetic_snapshot':snapshot}),
                      evidence_hash=_sha256({'evidence':[],'sources':[]}),
                      predictions={'action': 'market_reference', 'selection_id': 'synthetic-selection',
                                   'offer_id': 'synthetic-offer', 'market': '승패', 'selection': '홈',
                                   'odds': 1.75, 'probability': {'market': 0.5}},
                      ledger_sequence=len(records)+1, previous_record_hash=previous)
        record['record_hash'] = _sha256(record); previous = record['record_hash']; records.append(record)
    for i in range(settlement_count):
        event=f'synthetic-event-{i:05d}'; record=dict(schema_version=SCHEMA_VERSION,record_type='settlement',
            settlement_id=f'synthetic-settlement-{i:05d}',settlement_version='synthetic-v1',snapshot_id=latest[event],
            settled_at='2026-01-03T00:00:00Z',captured_at='2026-01-03T00:00:00Z',
            outcome={'result':'hit','selection_id':'synthetic-selection'},source={'name':'synthetic'},
            ledger_sequence=len(records)+1,previous_record_hash=previous)
        record['record_hash']=_sha256(record); previous=record['record_hash']; records.append(record)
    assert db.mirror_prediction_records(records)==len(records)
    with db.transaction() as c:
        c.executemany('INSERT INTO match_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [('synthetic',2025,1,str(i),'2025-01-01T12:00:00','KBO','bs',f'합성홈{i}',f'합성원정{i}',1,0,
              '2025-01-01T16:00:00+00:00','confirmed') for i in range(history_count)])
        chars=c.execute("SELECT length(payload_json) FROM artifacts WHERE name='picks_v2'").fetchone()[0]
        counts=dict(c.execute('SELECT record_type, count(*) FROM prediction_records GROUP BY record_type').fetchall())
        ledger_chars=dict(c.execute('SELECT record_type, sum(length(record_json)) FROM prediction_records GROUP BY record_type').fetchall())
    return {'seed_sha256':hashlib.sha256(original).hexdigest(),'seed_games':len(games),'synthetic_live':live_count,
            'synthetic_past':past_count,'synthetic_unique_events':total,'picks_json_characters':chars,
            'ledger_counts':counts,'ledger_json_characters':ledger_chars,'history_rows':history_count,'synthetic':True}


def child(mode, path, output):
    try:
        os.environ['PROODD_DB_PATH'] = str(path)
        def no_network(*args, **kwargs): raise RuntimeError('network disabled in synthetic benchmark')
        socket.socket.connect = no_network; socket.create_connection = no_network
        from runtime_db import RuntimeDatabase
        import live_market_refresh as refresh
        refresh.PREDICTION_LEDGER = path.parent / 'synthetic-prediction-ledger.jsonl'
        from prediction_ledger import PredictionLedger
        original_verify=PredictionLedger._read_verified_storage
        original_get=RuntimeDatabase.get_artifact; original_records=RuntimeDatabase.prediction_records
        original_persist=refresh.persist_artifact
        original_store=RuntimeDatabase.store_artifact
        def baseline_store(db, name, payload):
            if name != 'picks_v2':
                return original_store(db, name, payload)
            body=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False)
            with db.transaction() as c:
                stamp=db._next_artifact_stamp(c,name)
                c.execute('''INSERT INTO artifacts(name,generated_at,payload_json,stored_at)
                    VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                    generated_at=excluded.generated_at,payload_json=excluded.payload_json,
                    stored_at=excluded.stored_at''',(name,payload.get('generated_at'),body,stamp))
        if mode=='before': RuntimeDatabase.store_artifact=baseline_store
        phases={}
        def measured_get(db, name):
            if mode=='before':
                with db.connect() as c: row=c.execute('SELECT payload_json FROM artifacts WHERE name=?',(name,)).fetchone()
                value=json.loads(row['payload_json']) if row else None
                del row
            else: value=original_get(db,name)
            phases['load']=memory(); return value
        def measured_records(db):
            # Baseline already used a cursor, but did not share immutable strings
            # across separate decoded records. No fetchall baseline is invented.
            if mode=='before':
                with db.connect() as c:
                    value=[json.loads(row['record_json']) for row in c.execute(
                        'SELECT record_json FROM prediction_records ORDER BY ledger_sequence')]
            else:
                value=original_records(db)
            phases['ledger_load']=memory(); return value
        def measured_verify(ledger):
            value=original_verify(ledger); phases['ledger_verified']=memory(); return value
        def measured_persist(*args,**kwargs):
            result=original_persist(*args,**kwargs); phases['serialize_persist']=memory(); return result
        RuntimeDatabase.get_artifact=measured_get; RuntimeDatabase.prediction_records=measured_records
        PredictionLedger._read_verified_storage=measured_verify
        refresh.persist_artifact=measured_persist
        started=time.monotonic(); code=refresh.refresh_once({'generated_at':'2026-08-30T01:00:00+00:00','markets':{}})
        elapsed=time.monotonic()-started; core_memory=memory()
        db=RuntimeDatabase();
        with db.connect() as c:
            artifact=c.execute("SELECT payload_json FROM artifacts WHERE name='picks_v2'").fetchone()[0]
            rows=c.execute("SELECT record_type,record_hash FROM prediction_records ORDER BY ledger_sequence").fetchall()
        document=json.loads(artifact); attached=sum(1 for g in document['live']+document['past'] if (g.get('prediction_record') or {}).get('result')=='hit')
        output.put({'mode':mode,'exit_code':code,'phases':phases,'whole_process':core_memory,'seconds':elapsed,
            'artifact_sha256':hashlib.sha256(artifact.encode('utf8')).hexdigest(),
            'ledger_sha256':hashlib.sha256(''.join(r['record_hash'] for r in rows).encode()).hexdigest(),
            'settlements_preserved':sum(r['record_type']=='settlement' for r in rows),'attached_hits':attached})
    except BaseException as exc:
        output.put({'mode':mode,'error':repr(exc)})


def response_child(mode, path, output):
    try:
        from runtime_db import RuntimeDatabase
        db=RuntimeDatabase(path)
        if mode=='before':
            payload, revision=db.get_artifact_json('picks_v2')
            raw=payload.encode('utf8')
            body=gzip.compress(raw,compresslevel=3)
            tag='W/"'+hashlib.sha256(raw).hexdigest()+'"'
            del payload,raw
        else:
            body, revision, tag=db.get_artifact_gzip('picks_v2')
        output.put({'mode':mode,'memory':memory(),'etag':tag,'wire_bytes':len(body)})
    except BaseException as exc:
        output.put({'mode':mode,'error':repr(exc)})


def run():
    # The only input is the repository fixture; both copies stay in a fresh temp directory.
    with tempfile.TemporaryDirectory(prefix='synthetic-refresh-') as tmp:
        root=Path(tmp); base=root/'seed.sqlite3'; info=create_fixture(base,ROOT/'docs/data/picks_v2.json')
        results=[]; response_results=[]; ctx=mp.get_context('spawn')
        for mode in ('before','after'):
            q=ctx.Queue(); proc=ctx.Process(target=response_child,args=(mode,base,q)); proc.start(); proc.join(120)
            if proc.is_alive(): proc.terminate(); proc.join(); raise RuntimeError(mode+' response timeout')
            if proc.exitcode!=0: raise RuntimeError(mode+' response child failed')
            item=q.get(timeout=5); q.close()
            if 'error' in item: raise RuntimeError(str(item))
            response_results.append(item)
        if response_results[0]['etag']!=response_results[1]['etag']:
            raise RuntimeError('response raw-byte mismatch')
        for mode in ('before','after'):
            target=root/(mode+'.sqlite3'); shutil.copyfile(base,target)
            q=ctx.Queue(); proc=ctx.Process(target=child,args=(mode,target,q)); proc.start(); proc.join(120)
            if proc.is_alive(): proc.terminate(); proc.join(); raise RuntimeError(mode+' timeout after 120 seconds')
            if proc.exitcode!=0: raise RuntimeError(mode+' child failed: '+str(proc.exitcode))
            result=q.get(timeout=5); q.close()
            if 'error' in result or result.get('exit_code')!=0: raise RuntimeError(str(result))
            results.append(result)
        with closing(sqlite3.connect(root/'before.sqlite3')) as a, closing(sqlite3.connect(root/'after.sqlite3')) as b:
            artifact_a=a.execute("SELECT payload_json FROM artifacts WHERE name='picks_v2'").fetchone()[0]
            artifact_b=b.execute("SELECT payload_json FROM artifacts WHERE name='picks_v2'").fetchone()[0]
            from itertools import zip_longest
            records_a=a.execute('SELECT record_json FROM prediction_records ORDER BY ledger_sequence')
            records_b=b.execute('SELECT record_json FROM prediction_records ORDER BY ledger_sequence')
            same_records=all(x==y for x,y in zip_longest(records_a,records_b))
            same=artifact_a==artifact_b and same_records
        if results[0]['settlements_preserved']!=info['ledger_counts']['settlement'] or results[0]['attached_hits']!=info['ledger_counts']['settlement']:
            raise RuntimeError('synthetic settlements not preserved and attached')
        print(json.dumps({'fixture':info,'runs':results,'response_runs':response_results,'exact_outputs_equal':same,
            'limitations':'synthetic workload with empty live market feed; no new predictions, no provider history query; after uses checked-out code, which may not contain streaming fix; cumulative process peaks; no production DB, network or real capacity claim'},ensure_ascii=False,indent=2))
        if not same: raise RuntimeError('output mismatch')


if __name__=='__main__':
    if len(sys.argv)!=1: raise SystemExit('No database or external input arguments are accepted')
    mp.freeze_support(); run()
