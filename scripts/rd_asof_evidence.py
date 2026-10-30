"""Research-only as-of gates and append-only LOCAL ingestion journal.

The journal proves consistency of locally recorded bytes/time, not when an
external publisher first posted them. It cannot make historical captures pregame.
No runtime DB, scheduler or production services are imported.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path


def timestamp(value):
    if not isinstance(value,str):raise ValueError('timezone-aware timestamp required')
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.utcoffset() is None:raise ValueError('timezone-aware timestamp required')
    return t.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                                     separators=(',',':'),allow_nan=False).encode()).hexdigest()


def is_hash(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def load_journal(path, verify_objects=True):
    path=Path(path);records=[];previous=None
    if not path.exists():return records
    with path.open(encoding='utf-8') as f:
        for n,line in enumerate(f,1):
            r=json.loads(line)
            hashed={k:v for k,v in r.items() if k!='record_hash'}
            if r.get('sequence')!=n or r.get('parent_hash')!=previous or digest(hashed)!=r.get('record_hash'):
                raise ValueError(f'journal chain mismatch at record {n}')
            timestamp(r['observed_at'])
            if not is_hash(r.get('source_sha256')):raise ValueError('invalid source hash')
            if verify_objects:
                obj=(path.parent/r['object_path']).resolve()
                if not obj.is_relative_to((path.parent/'objects').resolve()):raise ValueError('object path outside journal objects')
                if hashlib.sha256(obj.read_bytes()).hexdigest()!=r['source_sha256']:
                    raise ValueError(f'captured object mismatch at record {n}')
            records.append(r);previous=r['record_hash']
    return records


@contextmanager
def journal_lock(path):
    lock=Path(str(path)+'.lock')
    descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()  # Only the lock created by this call; never steal another lock.


def capture_local_file(source,journal,*,fixture,kind,source_url=None,max_fact_time=None,clock=None):
    """No backdating argument in the CLI. Clock injection exists only for tests."""
    if kind not in ('input','quote','result'):raise ValueError('unknown capture kind')
    if not fixture:raise ValueError('fixture identity required')
    observed=(clock() if clock else datetime.now(timezone.utc))
    if observed.utcoffset() is None:raise ValueError('capture clock requires timezone')
    observed=observed.astimezone(timezone.utc)
    if max_fact_time is not None and timestamp(max_fact_time)>observed:
        raise ValueError('future fact cannot be recorded as already observed')
    source=Path(source);journal=Path(journal)
    payload=source.read_bytes();sha=hashlib.sha256(payload).hexdigest()
    journal.parent.mkdir(parents=True,exist_ok=True)
    with journal_lock(journal):
        records=load_journal(journal)
        objects=journal.parent/'objects';objects.mkdir(exist_ok=True)
        obj=objects/sha
        if obj.exists():
            if hashlib.sha256(obj.read_bytes()).hexdigest()!=sha:raise ValueError('object content collision')
        else:
            with obj.open('xb') as f:f.write(payload)
        r=dict(version=1,sequence=len(records)+1,parent_hash=records[-1]['record_hash'] if records else None,
               fixture=fixture,kind=kind,observed_at=observed.isoformat(),source_sha256=sha,
               object_path='objects/'+sha,source_url=source_url,max_fact_time=max_fact_time,
               observation_semantics='local_file_ingestion_now_not_authenticated_remote_publication')
        r['record_hash']=digest(r)
        with journal.open('a',encoding='utf-8',newline='\n') as f:
            f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')
            f.flush();os.fsync(f.fileno())
    return r


def journal_registry(paths):
    result={}
    for path in paths:
        for r in load_journal(path):
            if r['record_hash'] in result and result[r['record_hash']]!=r:
                raise ValueError('conflicting journal record identity')
            result[r['record_hash']]=r
    return result


def feature_gate(value,evidence,decision_at,*,view='retrospective_lagged',fixture=None,registry=None):
    """Return (accepted, reason). Missing values are never converted to zero."""
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        return False,'missing_or_nonfinite_value'
    if not isinstance(evidence,dict):return False,'missing_evidence'
    try:decision=timestamp(decision_at)
    except (ValueError,TypeError):return False,'missing_or_naive_decision_time'
    date_only=False
    try:fact=timestamp(evidence.get('max_fact_time'))
    except (ValueError,TypeError):
        if evidence.get('max_fact_time') is not None:return False,'invalid_nonnull_fact_time'
        if view!='retrospective_date_lagged':return False,'missing_or_naive_fact_time'
        try:
            day=date.fromisoformat(evidence['max_fact_date'])
            selected=evidence.get('selected_fact_dates')
            if selected is not None:
                if not isinstance(selected,list) or not selected or max(date.fromisoformat(x) for x in selected)!=day:
                    return False,'selected_fact_dates_mismatch'
            tz={'UTC':timezone.utc,'Asia/Seoul':timezone(timedelta(hours=9))}[evidence['fact_date_timezone']]
            # This is an explicit bound ASSUMPTION, never a fabricated source timestamp.
            upper=datetime.combine(day+timedelta(days=1),datetime.min.time(),tz)
            if upper+timedelta(hours=24)>decision:return False,'date_history_not_lagged_full_intervening_day'
        except (KeyError,ValueError,TypeError):return False,'missing_fact_date_or_timezone'
        date_only=True;fact=upper
    if fact>decision:return False,'future_fact'
    hashes=evidence.get('source_sha256')
    hashes=[hashes] if isinstance(hashes,str) else hashes
    if not isinstance(hashes,list) or not hashes or not all(is_hash(h)for h in hashes):
        return False,'missing_content_hash'
    tier=evidence.get('availability_tier')
    if tier not in ('captured_pregame','retrospective_lagged'):return False,'unverified_feature_tier'
    if view in ('retrospective_lagged','retrospective_date_lagged'):
        return True,('calendar_end_plus24h_assumption_not_actual_timestamp' if date_only else
                     'structural_past_only_not_receipt_certified')
    if view!='captured_pregame':raise ValueError('unknown evidence view')
    if tier!='captured_pregame':return False,'not_captured_pregame'
    try:observed=timestamp(evidence.get('observed_at'))
    except (ValueError,TypeError):return False,'missing_or_naive_observed_time'
    if observed>decision:return False,'observed_after_decision'
    if fact>observed:return False,'fact_after_observation'
    links=evidence.get('capture_record_hashes') or ([evidence['capture_record_hash']] if evidence.get('capture_record_hash') else [])
    if not links or not registry:return False,'missing_verified_local_capture_link'
    captured_hashes=set()
    for link in links:
        r=registry.get(link)
        if not r or r['kind']!='input':return False,'unknown_input_capture_link'
        if fixture is not None and r['fixture']!=fixture:return False,'capture_fixture_mismatch'
        if timestamp(r['observed_at'])>decision:return False,'capture_after_decision'
        if timestamp(r['observed_at'])>observed:return False,'feature_observation_before_input_capture'
        captured_hashes.add(r['source_sha256'])
    if not set(hashes)<=captured_hashes:return False,'uncaptured_source_hash'
    return True,'locally_captured_before_decision_not_third_party_certified'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('capture')
    p.add_argument('--source',required=True,type=Path);p.add_argument('--journal',required=True,type=Path)
    p.add_argument('--fixture',required=True);p.add_argument('--kind',choices=('input','quote','result'),required=True)
    p.add_argument('--source-url');p.add_argument('--max-fact-time')
    p=sub.add_parser('verify');p.add_argument('--journal',required=True,type=Path)
    args=parser.parse_args()
    if args.command=='capture':
        record=capture_local_file(args.source,args.journal,fixture=args.fixture,kind=args.kind,
                                  source_url=args.source_url,max_fact_time=args.max_fact_time)
        print(json.dumps({k:record[k]for k in ('record_hash','fixture','kind','observed_at','source_sha256')},ensure_ascii=False))
    else:print(json.dumps(dict(valid=True,records=len(load_journal(args.journal)))))


if __name__=='__main__':main()
