import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import runtime_db
from runtime_db import RuntimeDatabase


def stored(db):
    with db.connect() as c:
        return tuple(c.execute("SELECT payload_json,typeof(payload_json),stored_at FROM artifacts WHERE name='picks_v2'").fetchone())


def test_stream_write_keeps_exact_text_type_revision_and_snapshot(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'db.sqlite3')
    old = {'generated_at': 'old', 'live': []}
    db.store_artifact('picks_v2', old)
    previous = stored(db)
    payload = {'generated_at': 'new', 'live': [{'한글😀': '⚾' * 40000, 'integer': 10 ** 45,
        'float': -0.0, 'yes': True, 'no': False, 'missing': None}]}
    original = runtime_db._json_chunks
    calls = 0
    def chunks(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert stored(db) == previous  # Another connection sees committed old text.
        yield from original(value)
    monkeypatch.setattr(runtime_db, '_json_chunks', chunks)
    db.store_artifact('picks_v2', payload)
    body, kind, stamp = stored(db)
    assert body == json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    assert kind == 'text' and stamp > previous[2]
    assert db.get_artifact('picks_v2') == payload


@pytest.mark.parametrize('failure', ['nan', 'short', 'long', 'midwrite', 'spoolwrite'])
def test_failed_stream_write_rolls_back_body_and_revision(tmp_path, monkeypatch, failure):
    db = RuntimeDatabase(tmp_path / 'db.sqlite3')
    db.store_artifact('picks_v2', {'generated_at': 'old', 'live': []})
    before = stored(db)
    factory = runtime_db.tempfile.TemporaryFile
    class BrokenSpool:
        def __init__(self):self.file=factory(mode='w+b');self.reads=0
        def __enter__(self):return self
        def __exit__(self,*args):self.file.close()
        def write(self,data):
            if failure=='spoolwrite':raise OSError('synthetic spool full')
            return self.file.write(data)
        def seek(self,*args):return self.file.seek(*args)
        def read(self,n):
            self.reads+=1
            assert stored(db)==before  # Reader sees old committed body even mid-copy.
            if failure=='short':return b''
            if failure=='long':return b'x'*10000
            if failure=='midwrite':
                if self.reads==1:return self.file.read(1)
                raise OSError('synthetic interrupted copy')
            return self.file.read(n)
    payload = {'live': [1, 2, 3]}
    if failure=='nan':payload['bad']=float('nan')
    else:monkeypatch.setattr(runtime_db.tempfile,'TemporaryFile',lambda **kw:BrokenSpool())
    with pytest.raises((ValueError,OSError)):
        db.store_artifact('picks_v2',payload)
    assert stored(db)==before


def test_picks_encode_once_before_sqlite_writer_is_reserved(tmp_path,monkeypatch):
    from contextlib import contextmanager
    db=RuntimeDatabase(tmp_path/'db.sqlite3')
    original_chunks=runtime_db._json_chunks
    original_transaction=db.transaction
    reserved=False; calls=[]
    @contextmanager
    def transaction():
        nonlocal reserved
        with original_transaction() as connection:
            reserved=True
            try:yield connection
            finally:reserved=False
    def chunks(payload):
        assert not reserved, 'JSON encoding holds the SQLite writer'
        calls.append(1)
        yield from original_chunks(payload)
    monkeypatch.setattr(db,'transaction',transaction)
    monkeypatch.setattr(runtime_db,'_json_chunks',chunks)
    payload={'generated_at':'new','live':[{'한글':'⚾'*10000}]}
    db.store_artifact('picks_v2',payload)
    assert len(calls)==1
    assert db.get_artifact('picks_v2')==payload
