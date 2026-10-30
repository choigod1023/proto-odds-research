from datetime import datetime,timezone
from pathlib import Path
import json,sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import rd_asof_evidence as e

OBS=datetime(2026,10,30,3,tzinfo=timezone.utc)
DEC='2026-10-30T04:00:00+00:00'


def captured(tmp_path):
    source=tmp_path/'raw.json';source.write_text('{"fixture":"A"}',encoding='utf-8')
    path=tmp_path/'journal.jsonl'
    record=e.capture_local_file(source,path,fixture='A',kind='input',max_fact_time='2026-10-29T20:00:00Z',clock=lambda:OBS)
    meta=dict(max_fact_time=record['max_fact_time'],observed_at=record['observed_at'],source_sha256=[record['source_sha256']],
              availability_tier='captured_pregame',capture_record_hash=record['record_hash'])
    return source,path,record,meta


def test_capture_retains_original_bytes_and_chain(tmp_path):
    source,path,first,meta=captured(tmp_path)
    source.write_text('changed',encoding='utf-8')
    second=e.capture_local_file(source,path,fixture='A',kind='result',clock=lambda:OBS)
    records=e.load_journal(path)
    assert len(records)==2 and second['parent_hash']==first['record_hash']
    assert (path.parent/first['object_path']).read_text(encoding='utf-8')=='{"fixture":"A"}'
    assert second['source_sha256']!=first['source_sha256']


def test_tampered_journal_and_object_are_detected(tmp_path):
    source,path,record,meta=captured(tmp_path)
    altered=dict(record,fixture='other')
    path.write_text(json.dumps(altered)+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='chain'):e.load_journal(path)
    path.write_text(json.dumps(record)+'\n',encoding='utf-8')
    (path.parent/record['object_path']).write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='object'):e.load_journal(path)


def test_does_not_steal_existing_writer_lock(tmp_path):
    source,path,record,meta=captured(tmp_path)
    lock=Path(str(path)+'.lock');lock.write_text('other worker',encoding='utf-8')
    with pytest.raises(FileExistsError):e.capture_local_file(source,path,fixture='A',kind='input',clock=lambda:OBS)
    assert lock.read_text()=='other worker' and len(e.load_journal(path))==1


def test_strict_gate_requires_matching_captured_bytes_fixture_and_clock(tmp_path):
    source,path,record,meta=captured(tmp_path);registry=e.journal_registry([path])
    assert e.feature_gate(0,meta,DEC,view='captured_pregame',fixture='A',registry=registry)[0]
    assert not e.feature_gate(0,meta,DEC,view='captured_pregame',fixture='B',registry=registry)[0]
    assert not e.feature_gate(0,dict(meta,source_sha256=['a'*64]),DEC,view='captured_pregame',fixture='A',registry=registry)[0]
    assert not e.feature_gate(0,meta,'2026-10-30T02:00:00Z',view='captured_pregame',fixture='A',registry=registry)[0]


def test_strict_observation_cannot_precede_fact_or_linked_capture(tmp_path):
    source,path,record,meta=captured(tmp_path);registry=e.journal_registry([path])
    late_fact=dict(meta,max_fact_time='2026-10-30T03:30:00Z')
    assert e.feature_gate(1,late_fact,DEC,view='captured_pregame',fixture='A',registry=registry)==(False,'fact_after_observation')
    early_observation=dict(meta,observed_at='2026-10-30T02:45:00Z')
    assert e.feature_gate(1,early_observation,DEC,view='captured_pregame',fixture='A',registry=registry)==(False,'feature_observation_before_input_capture')


def test_past_source_captured_after_decision_does_not_become_pregame(tmp_path):
    source,path,record,meta=captured(tmp_path)
    old='2026-10-29T22:00:00Z'
    assert not e.feature_gate(2,meta,old,view='captured_pregame',fixture='A',registry=e.journal_registry([path]))[0]
    assert e.feature_gate(2,dict(meta,availability_tier='retrospective_lagged'),old,view='retrospective_lagged')[0]


def test_missing_numeric_and_future_facts_fail_closed(tmp_path):
    source,path,record,meta=captured(tmp_path)
    for value in (None,True,float('nan'),float('inf'),'0'):
        assert not e.feature_gate(value,meta,DEC)[0]
    assert not e.feature_gate(1,dict(meta,max_fact_time='2026-10-31T00:00:00Z'),DEC)[0]
    assert not e.feature_gate(1,dict(meta,max_fact_time='2026-10-29T20:00:00'),DEC)[0]


def test_date_only_is_explicit_separate_assumption_and_not_a_timestamp():
    meta=dict(max_fact_time=None,max_fact_date='2026-10-27',fact_date_timezone='UTC',observed_at=None,
              source_sha256=['a'*64],availability_tier='retrospective_lagged')
    assert not e.feature_gate(1,meta,DEC,view='captured_pregame')[0]
    assert not e.feature_gate(1,meta,DEC,view='retrospective_lagged')[0]
    ok,reason=e.feature_gate(1,meta,DEC,view='retrospective_date_lagged')
    assert ok and 'assumption' in reason and meta['max_fact_time'] is None
    assert not e.feature_gate(1,dict(meta,max_fact_date='2026-10-29'),DEC,view='retrospective_date_lagged')[0]
    assert not e.feature_gate(1,dict(meta,fact_date_timezone=None),DEC,view='retrospective_date_lagged')[0]


def test_capture_rejects_future_fact_without_writing(tmp_path):
    source=tmp_path/'s';source.write_text('{}')
    with pytest.raises(ValueError,match='future fact'):
        e.capture_local_file(source,tmp_path/'j',fixture='A',kind='input',max_fact_time='2026-11-01T00:00:00Z',clock=lambda:OBS)
    assert not (tmp_path/'j').exists()


def test_bad_timestamp_cannot_fall_back_to_an_older_date():
    meta=dict(max_fact_time='2026-11-10T00:00:00',max_fact_date='2026-10-27',fact_date_timezone='UTC',
              source_sha256=['a'*64],availability_tier='retrospective_lagged')
    assert e.feature_gate(1,meta,DEC,view='retrospective_date_lagged')==(False,'invalid_nonnull_fact_time')


def test_calendar_evidence_must_match_actual_selected_dates():
    meta=dict(max_fact_time=None,max_fact_date='2026-10-27',fact_date_timezone='UTC',selected_fact_dates=['2026-10-30'],
              source_sha256=['a'*64],availability_tier='retrospective_lagged')
    assert e.feature_gate(1,meta,DEC,view='retrospective_date_lagged')==(False,'selected_fact_dates_mismatch')
