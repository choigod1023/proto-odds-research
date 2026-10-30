import copy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'experiments' / 'accuracy-structure-20261030'
spec = importlib.util.spec_from_file_location('structure_validator', ROOT / 'validator.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

def fixture(name='pregame'):
    return json.loads((ROOT / ('fixture-' + name + '.json')).read_text(encoding='utf-8'))

def test_valid_append():
    assert v.validate_append(fixture(), fixture('settled'))

def test_void_without_result():
    d=fixture('settled'); d['settlements'][0].update(status='void', score=None)
    assert v.validate(d)

@pytest.mark.parametrize('mutation', [
    lambda d: d['games'][0].pop('kickoff_at'),
    lambda d: d['games'][0].update(kickoff_at='2026-10-30T10:00:00'),
    lambda d: d['observations'][0]['evidence'].update(revision_received_at='2026-10-30T08:00:00+00:00'),
    lambda d: d['observations'][0]['evidence'].update(revision_received_at='2026-10-30T09:31:00+00:00'),
    lambda d: d['observations'][0]['evidence'].update(external_receipt_verified=True),
    lambda d: d['observations'][0]['evidence'].update(availability_tier='retrospective'),
    lambda d: d['quotes'][0]['identity'].update(period='half'),
    lambda d: d['quotes'][0]['identity'].update(line='1.5'),
    lambda d: d['quotes'][0]['odds'].pop('away'),
    lambda d: d['freezes'][0].update(selection='away'),
    lambda d: d['games'][0].update(status='cancelled'),
    lambda d: d['lineups'][0]['players'][0].update(player_id=''),
    lambda d: d['freezes'][0].update(decision_at='2026-10-30T10:00:00+00:00'),
    lambda d: d['aliases'].append(copy.deepcopy(d['aliases'][0])),
    lambda d: d['freezes'][0].update(result={'home':3}),
])
def test_invalid(mutation):
    d=fixture(); mutation(d)
    with pytest.raises(v.Invalid): v.validate(d)

def test_rehash_does_not_allow_mutating_history():
    before=fixture(); after=copy.deepcopy(before); after['freezes'][0]['selection']='away'; after['freezes'][0]['freeze_hash']=v.digest(after['freezes'][0]); assert v.validate(after)
    with pytest.raises(v.Invalid): v.validate_append(before,after)

def test_settlement_scope_and_timing():
    for field, value in [('settlement_scope','half'),('final_at','2026-10-30T14:00:00+00:00')]:
        d=fixture('settled'); d['settlements'][0][field]=value
        with pytest.raises(v.Invalid): v.validate(d)

def test_doubleheader_and_reissue_are_distinct():
    d=fixture(); g=copy.deepcopy(d['games'][0]); g.update(canonical_game_id='g2',doubleheader_game_number=2); d['games'].append(g)
    d['aliases'].append({'canonical_game_id':'g2','authority':'official','official_id':'game-2'})
    p=copy.deepcopy(d['products'][0]); p['identity']['product_id']='p2'; p['reissue_of']='p1'; d['products'].append(p)
    assert v.validate(d)
    d['aliases'][1]['official_id']='game-1'
    with pytest.raises(v.Invalid): v.validate(d)

@pytest.mark.parametrize('mutation', [
    lambda d: d['quotes'][0]['odds'].update(home=9.9),
    lambda d: d['observations'][0]['evidence'].update(source_sha256='b' * 64),
    lambda d: d['lineups'][0]['players'][0].update(role='reliever'),
])
def test_referenced_content_changes_fail_standalone(mutation):
    d=fixture(); mutation(d)
    with pytest.raises(v.Invalid, match='input content hash mismatch'):
        v.validate(d)

def test_recomputed_all_hashes_require_trusted_snapshot():
    before=fixture(); after=copy.deepcopy(before)
    after['quotes'][0]['odds']['home']=9.9
    observations={o['observation_id']:o for o in after['observations']}
    f=after['freezes'][0]
    f['input_content_sha256']=v.content_digest(after['quotes'][0], after['lineups'], observations)
    f['freeze_hash']=v.digest(f)
    assert v.validate(after)
    with pytest.raises(v.Invalid, match='history changed'):
        v.validate_append(before, after)

def test_v1_is_not_silently_accepted_as_v1_1():
    d=fixture(); d['schema_version']='v1'; d['freezes'][0].pop('input_content_sha256')
    with pytest.raises(v.Invalid): v.validate(d)

def test_content_digest_matches_independent_canonical_encoding():
    import hashlib
    d=fixture(); f=d['freezes'][0]; o=d['observations'][0]
    payload={'domain':'accuracy-structure-v1.1-inputs','quote':d['quotes'][0],
             'quote_observation':o,'lineups':[{'lineup':d['lineups'][0], 'observation':o}]}
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode('utf-8')
    assert hashlib.sha256(raw).hexdigest()==f['input_content_sha256']
