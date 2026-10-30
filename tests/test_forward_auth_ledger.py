"""Only this isolated study's files are imported; no production writes/network."""
import base64
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

STUDY = Path(__file__).resolve().parents[1]/"experiments/forward-auth-ledger-20261030"
# Unique module aliases avoid collisions with other research and production modules.
def module(name, file):
    spec = importlib.util.spec_from_file_location(name, STUDY/file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

ledger = module("forward_auth_ledger", "ledger.py")
independent = module("forward_auth_independent", "independent.py")


@pytest.fixture
def payload():
    return dict(event_id="TEST", product="moneyline", league="TEST", kickoff="2026-10-30T12:00:00Z",
                received_at="2026-10-30T11:00:00Z", source_observed_at="2026-10-30T10:59:59Z",
                source_url="https://example.invalid/test", source_sha256=hashlib.sha256(b"raw").hexdigest(),
                source_kind="raw_response", odds=dict(home=1.8,away=2.0),
                probabilities={"market":dict(home=.53,away=.47)}, votes=[])


@pytest.fixture
def frozen(tmp_path,payload):
    file = ledger.Ledger(tmp_path/"ledger.jsonl", "TEST-LEDGER")
    c = file.append("capture",payload,payload["received_at"],source_bytes=b"raw")
    f = file.append("freeze",dict(event_id="TEST",product="moneyline",capture_hash=c["record_hash"],
                    cutoff_at="2026-10-30T11:30:00Z"),"2026-10-30T11:29:00Z")
    return file,c,f


def signed(records, ledger_id, *, time="2026-10-30T11:29:30Z", witness="TEST-WITNESS"):
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519",
                                reason="install study requirements to exercise signature verification")
    key = ed25519.Ed25519PrivateKey.generate()
    trust = {witness:dict(public_key_hex=key.public_key().public_bytes_raw().hex(),
             valid_from="2026-10-01T00:00:00Z", valid_until="2026-11-01T00:00:00Z", independent=True)}
    cp = ledger.checkpoint_message(records,ledger_id,witness,time)
    cp["signature_b64"] = base64.b64encode(key.sign(ledger.canonical(cp))).decode()
    return cp,trust,key


def test_append_freeze_settle_independent(frozen):
    file,c,f = frozen
    before = file.path.read_bytes()
    file.append("settlement",dict(event_id="TEST",product="moneyline",freeze_hash=f["record_hash"],winner="home",
                final_at="2026-10-30T14:00:00Z", source_sha256=hashlib.sha256(b"result").hexdigest()),"2026-10-30T14:01:00Z")
    assert file.path.read_bytes().startswith(before)
    assert independent.verify_chain_independent(file.read(),file.ledger_id) == 3
    assert file.read()[0] == c and file.read()[1] == f


@pytest.mark.parametrize("field",["winner","label","home_score","away_score","postgame","current_final_odds","result","outcome"])
def test_no_label_postgame_final_odds_fields(payload,field):
    payload[field] = "must reject"
    with pytest.raises(ledger.InvalidLedger):
        ledger.capture_valid(payload)


@pytest.mark.parametrize("mutation",["late","future","naive","bad_hash","bad_odds","nan","bad_probs","prob_label","archive","mixed_roster"])
def test_capture_leakage_and_schema(payload,mutation):
    if mutation == 'late':payload['received_at'] = '2026-10-30T11:30:00.001Z'
    if mutation == 'future':payload['source_observed_at'] = '2026-10-30T11:01:00Z'
    if mutation == 'naive':payload['received_at'] = '2026-10-30T11:00:00'
    if mutation == 'bad_hash':payload['source_sha256'] = 'fake'
    if mutation == 'bad_odds':payload['odds']['home'] = 1
    if mutation == 'nan':payload['odds']['home'] = float('nan')
    if mutation == 'bad_probs':payload['probabilities']['market']['home'] = .9
    if mutation == 'prob_label':payload['probabilities']['winner'] = {'home':1,'away':0}
    if mutation == 'archive':payload['source_kind'] = 'retrospective_archive'
    if mutation == 'mixed_roster':payload['odds'] = {'home':1.8,'over':2.0}
    with pytest.raises(ledger.InvalidLedger):ledger.capture_valid(payload)


def test_raw_hash_must_match_actual_bytes(tmp_path,payload):
    file = ledger.Ledger(tmp_path/'l.jsonl','L')
    with pytest.raises(ledger.InvalidLedger):
        file.append('capture',payload,payload['received_at'],source_bytes=b'changed')
    assert not file.path.exists()


@pytest.mark.parametrize('field',['home_score','result','final_odds','postgame'])
def test_nested_vote_label_rejected(payload,field):
    payload['votes'] = [dict(author_id='a',side='home',received_at='2026-10-30T10:55:00Z',
                        published_at='2026-10-30T10:54:00Z', source_sha256=hashlib.sha256(b'v').hexdigest(), **{field:1})]
    with pytest.raises(ledger.InvalidLedger):ledger.capture_valid(payload)


def test_postgame_vote_rejected(payload):
    payload['votes'] = [dict(author_id='a',side='home',received_at='2026-10-30T13:00:00Z',
                        published_at='2026-10-30T12:30:00Z', source_sha256=hashlib.sha256(b'v').hexdigest())]
    with pytest.raises(ledger.InvalidLedger):ledger.capture_valid(payload)


def test_t30_boundary_is_inclusive(payload):
    payload['received_at'] = '2026-10-30T11:30:00Z'
    ledger.capture_valid(payload)


def test_capture_after_freeze_rejected_without_rewrite(frozen,payload):
    file,_,_ = frozen
    payload['received_at'] = '2026-10-30T11:29:30Z'
    before=file.path.read_bytes()
    with pytest.raises(ledger.InvalidLedger):file.append('capture',payload,payload['received_at'],source_bytes=b'raw')
    assert file.path.read_bytes() == before


@pytest.mark.parametrize('mutation',['change','delete','reverse','truncate'])
def test_tamper_detection(frozen,mutation):
    file,_,_ = frozen
    rows=file.read()
    if mutation=='change':rows[0]['payload']['odds']['home']=3
    if mutation=='delete':rows=rows[1:]
    if mutation=='reverse':rows.reverse()
    if mutation=='truncate':
        file.path.write_bytes(file.path.read_bytes()[:-1])
        with pytest.raises(ledger.InvalidLedger):file.read()
        return
    with pytest.raises(ledger.InvalidLedger):ledger.verify_chain(rows,file.ledger_id)


def rewrite(rows):
    references={}
    for r in rows:
        old=r['record_hash']
        r['previous_hash']=references.get(r['previous_hash'])
        for key in ('capture_hash','freeze_hash'):
            if key in r['payload']:r['payload'][key]=references[r['payload'][key]]
        r['record_hash']=ledger.digest({k:v for k,v in r.items() if k!='record_hash'})
        references[old]=r['record_hash']
    return rows


def test_whole_history_rehash_passes_chain_but_fails_external_root(frozen):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id)
    rows=file.read()
    rows[0]['payload']['odds']['home']=3
    rows=rewrite(rows)
    ledger.verify_chain(rows,file.ledger_id)
    with pytest.raises(ledger.InvalidLedger,match='root mismatch'):
        ledger.verify_checkpoint(rows,file.ledger_id,cp,trust,test_only=True)
    cp['tail_hash']=rows[-1]['record_hash']
    with pytest.raises(ledger.InvalidLedger,match='signature'):
        ledger.verify_checkpoint(rows,file.ledger_id,cp,trust,test_only=True)


@pytest.mark.parametrize('field,value',[('witnessed_at','2026-10-30T11:28:00Z'),('size',1),('ledger_id','DIFFERENT'),('signature_b64','bad'),('verified',True)])
def test_checkpoint_rewrite_and_verified_flag_rejected(frozen,field,value):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id)
    cp[field]=value
    with pytest.raises(ledger.InvalidLedger):ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust,test_only=True)


def test_signed_but_clock_precedes_freeze_rejected(frozen):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id,time='2026-10-30T11:28:59Z')
    with pytest.raises(ledger.InvalidLedger,match='predates receipt'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust,test_only=True)


def test_production_no_external_service_fail_closed(frozen):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id)
    assert ledger.pinned_trust()=={}
    assert ledger.authenticated_freezes(file.read(),file.ledger_id,[])==[]
    # Neither caller roots nor a valid self-generated signature can authenticate.
    with pytest.raises(ledger.InvalidLedger,match='caller supplied'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust)
    with pytest.raises(ledger.InvalidLedger,match='untrusted witness'):
        ledger.authenticated_freezes(file.read(),file.ledger_id,[cp])
    result=ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust,test_only=True)
    assert result['authentication_scope']=='synthetic_test_only'


def test_verifier_policy_rewrite_rejected(monkeypatch):
    monkeypatch.setattr(ledger,'PINNED_POLICY_SHA256','0'*64)
    with pytest.raises(ledger.InvalidLedger,match='policy was rewritten'):ledger.pinned_trust()


@pytest.mark.parametrize('time,expected',[('2026-10-30T11:29:30Z',1),('2026-10-30T11:30:00Z',1),('2026-10-30T11:30:01Z',0)])
def test_verifier_owned_test_policy_requires_pre_cutoff_signature(frozen,monkeypatch,time,expected):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id,time=time)
    # Test-scoped verifier configuration, never a checkpoint/caller-supplied root.
    # The real policy file remains empty; this does not certify historical data.
    monkeypatch.setattr(ledger,'pinned_trust',lambda:trust)
    assert len(ledger.authenticated_freezes(file.read(),file.ledger_id,[cp]))==expected


def test_root_and_clock_without_external_signature_rejected(frozen):
    file,_,_=frozen
    cp,_,_=signed(file.read(),file.ledger_id)
    del cp['signature_b64']
    with pytest.raises(ledger.InvalidLedger):ledger.verify_checkpoint(file.read(),file.ledger_id,cp)


def test_unconfigured_verifier_fail_closed_without_optional_dependency(frozen,monkeypatch):
    file,_,_=frozen
    cp=ledger.checkpoint_message(file.read(),file.ledger_id,'UNCONFIGURED','2026-10-30T11:29:30Z')
    cp['signature_b64']=''
    monkeypatch.setitem(sys.modules,'cryptography',None)
    with pytest.raises(ledger.InvalidLedger,match='untrusted witness'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,cp)


def test_attacker_signature_using_same_witness_name_rejected(frozen):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id)
    fake_cp,_,_=signed(file.read(),file.ledger_id)
    with pytest.raises(ledger.InvalidLedger,match='signature'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,fake_cp,trust,test_only=True)


def test_witness_trust_interval_and_independence(frozen):
    file,_,_=frozen
    cp,trust,_=signed(file.read(),file.ledger_id)
    trust['TEST-WITNESS']['valid_until']='2026-10-30T10:00:00Z'
    with pytest.raises(ledger.InvalidLedger,match='trust interval'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust,test_only=True)
    trust['TEST-WITNESS']['independent']=False
    with pytest.raises(ledger.InvalidLedger,match='untrusted witness'):
        ledger.verify_checkpoint(file.read(),file.ledger_id,cp,trust,test_only=True)


def test_latest_revision_freeze_no_backtracking(tmp_path,payload):
    f=ledger.Ledger(tmp_path/'l.jsonl','L')
    a=f.append('capture',payload,payload['received_at'],source_bytes=b'raw')
    payload['received_at']='2026-10-30T11:10:00Z'
    f.append('capture',payload,payload['received_at'],source_bytes=b'raw')
    with pytest.raises(ledger.InvalidLedger,match='latest capture'):
        f.append('freeze',dict(event_id='TEST',product='moneyline',capture_hash=a['record_hash'],
                  cutoff_at='2026-10-30T11:30:00Z'),'2026-10-30T11:29:00Z')


def test_final_settlement_cannot_change_predictions(frozen):
    file,c,f=frozen
    before=ledger.predict_frozen(c['payload'])
    for winner in ('home','away'):
        altered=deepcopy(c['payload'])
        # Outcome lives only in separate settlement, never in feature payload.
        settlement=dict(event_id='TEST',product='moneyline',freeze_hash=f['record_hash'],winner=winner,
                        final_at='2026-10-30T14:00:00Z',source_sha256=hashlib.sha256(b'result').hexdigest())
        assert ledger.predict_frozen(altered)==before
        assert 'winner' not in altered and settlement['winner']==winner


def test_duplicate_or_early_settlement_rejected(frozen):
    file,_,f=frozen
    p=dict(event_id='TEST',product='moneyline',freeze_hash=f['record_hash'],winner='home',
           final_at='2026-10-30T11:59:00Z',source_sha256=hashlib.sha256(b'result').hexdigest())
    with pytest.raises(ledger.InvalidLedger):file.append('settlement',p,'2026-10-30T14:01:00Z')
    p['final_at']='2026-10-30T14:00:00Z'
    file.append('settlement',p,'2026-10-30T14:01:00Z')
    with pytest.raises(ledger.InvalidLedger):file.append('settlement',p,'2026-10-30T14:02:00Z')


def test_existing_corpus_no_certified_source_receipts():
    rows=[json.loads(s) for s in gzip.open(STUDY/'inputs/timed.jsonl.gz','rt',encoding='utf8')]
    result=independent.verify_historical(rows)
    assert result==dict(revisions=7458,fixtures=2640,internal_t30=7418,internal_t30_fixtures=2639,external_certified=0)


def load_replay():
    # Bind isolated dependencies only while loading this script; restore globals.
    saved={k:sys.modules.get(k) for k in ('ledger','independent')}
    sys.modules['ledger']=ledger
    sys.modules['independent']=independent
    try:return module('forward_auth_replay','replay.py')
    finally:
        for k,v in saved.items():
            if v is None:sys.modules.pop(k,None)
            else:sys.modules[k]=v


def test_heldout_label_flip_parameters_and_predictions_invariant():
    replay=load_replay()
    raw=json.loads((STUDY/'inputs/strict-results.json').read_text(encoding='utf8'))['event_ledger']
    rows=[deepcopy(e) for e in raw if e['kickoff'] and all(e['picks'].values())]
    for e in rows:e['evaluation_date']=ledger.stamp(e['kickoff']).date().isoformat()
    split=replay.split_dates(rows,'evaluation_date')
    ids={e['fixture_id'] for e in split['test']}
    before=replay.frozen_predictions(rows)
    for e in rows:
        if e['fixture_id'] in ids:e['winner']=next(s for s in e['order'] if s!=e['winner'])
    assert replay.frozen_predictions(rows)==before
    assert len(split['test'])==24
    assert max(e['evaluation_date'] for e in split['train']) < min(e['evaluation_date'] for e in split['validation'])
    assert max(e['evaluation_date'] for e in split['validation']) < min(e['evaluation_date'] for e in split['test'])


def test_independent_settlement_and_weekly_bootstrap():
    replay=load_replay()
    rows=json.loads((STUDY/'inputs/strict-results.json').read_text(encoding='utf8'))['event_ledger']
    rows=[deepcopy(e) for e in rows if e['kickoff'] and all(e['picks'].values())]
    for e in rows:e['evaluation_date']=ledger.stamp(e['kickoff']).date().isoformat()
    test=replay.split_dates(rows,'evaluation_date')['test']
    for name in ('market','market90_vote10'):
        assert replay.settlement(test,name)==independent.settle(test,name)
    m=replay.paired(test,'market','market90_vote10')
    assert m['independent_bootstrap_exact_agreement'] and m['n']==24
    assert m['accuracy_ci95'][0]<0<m['accuracy_ci95'][1]


def test_power_zero_rate_does_not_divide_by_zero():
    replay=load_replay()
    result=replay.power([],{'by_league':{'TEST':{'authenticated_fixtures_per_week':0}}})
    row=result['rows'][0]
    assert row['paired_n_q1']==8719 and row['weeks_needed'] is None
