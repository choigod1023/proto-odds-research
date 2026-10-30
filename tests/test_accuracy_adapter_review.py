import copy
import importlib.util
import json
from pathlib import Path
import types

import pytest

ROOT=Path(__file__).resolve().parents[1]/'experiments/accuracy-adapter-review-20261030/vendor'
source=(ROOT/'development/original-tests.txt').read_text(encoding='utf8')
source=source.replace("DIR = Path(__file__).resolve().parents[1] / 'experiments' / 'accuracy-development-20261030'", 'DIR = Path('+repr(str(ROOT/'development'))+')')
# The archived contract intentionally differs from the current adapter. Keep
# its relative imports out of the live test package's sys.modules namespace.
source=source.replace('accuracy_development', 'accuracy_adapter_review_vendor')
upstream=types.ModuleType('upstream_fixtures'); upstream.__file__=str(ROOT/'development/original-tests.txt')
exec(compile(source,upstream.__file__,'exec'),upstream.__dict__)

@pytest.mark.parametrize('mutation',[
 lambda c:c['product'].update(period='half'),
 lambda c:c['product'].update(market='handicap'),
 lambda c:c['team_mapping'].update(h='not-in-state'),
 lambda c:c['quote'].update(revision_received_at='2026-10-30T09:31:00+00:00'),
 lambda c:c.update(state_available_through='2026-10-31T00:00:00+00:00'),
])
def test_core_must_fail_closed_even_when_verifier_returns_true(mutation):
    s,d,c,verify=upstream.fixture();mutation(c)
    req={'identity':c['identity'],'decision_at':'2026-10-30T09:30:00+00:00','lineup':None}
    with pytest.raises(ValueError):upstream.infer(s,req,c,lambda *_: True)

def test_bad_player_inputs_discarded_with_same_baseline():
    s,d,c,v=upstream.fixture();base=upstream.run(s,d,c,v)
    for change in (lambda x:x['lineups'][0]['players'][0].update(expected_minutes_or_innings=None),lambda x:x['lineups'][0].update(canonical_game_id='wrong'),lambda x:x['lineups'][0]['players'][0].update(player_id='')):
        x=copy.deepcopy(d);change(x);out=upstream.run(s,x,c,v,['l1'])
        assert out['availability_reason']['code']=='lineup_invalid'
        assert out['input_revision'] is None and out['probability_vector']==base['probability_vector']

def test_true_callable_does_not_set_external_receipt_certification():
    s,d,c,v=upstream.fixture();out=upstream.run(s,d,c,lambda *_:True)
    assert out['core_contract']['quote']['external_receipt_verified'] is False
    assert 'external_receipt_verified' not in out and 'verified' not in out
    assert out['availability_reason']['code']=='lineup_unavailable'

def test_true_callable_cannot_prove_team_mapping():
    s,d,c,v=upstream.fixture(); correct=upstream.run(s,d,c,v)
    c['team_mapping']={'h':'Away Name','a':'Home Name'}
    with pytest.raises(ValueError):upstream.run(s,d,c,v)
    swapped=upstream.run(s,d,c,lambda *_:True)
    assert swapped['core_contract']['team_mapping']!=correct['core_contract']['team_mapping']
    assert swapped['probability_vector']!=correct['probability_vector']

def test_decision_is_not_part_of_verifier_contract():
    s,d,c,v=upstream.fixture()
    first=upstream.run(s,d,c,v,decision='2026-10-30T09:00:00+00:00')
    second=upstream.run(s,d,c,v,decision='2026-10-30T09:30:00+00:00')
    assert first['decision_at']!=second['decision_at'] and first['core_contract']==second['core_contract']
    assert 'decision_at' not in c

def test_provenance_claim_is_only_as_reliable_as_verifier():
    s,d,c,v=upstream.fixture();c['state_available_through']='2026-10-06T00:00:00+00:00'
    with pytest.raises(ValueError):upstream.run(s,d,c,v)
    out=upstream.run(s,d,c,lambda *_:True)
    assert out['core_contract']['state_available_through']=='2026-10-06T00:00:00+00:00'

def test_vendored_v1_rejects_updated_v11_schema():
    old=upstream.validator
    latest=json.loads((ROOT/'structure/schema.json').read_text(encoding='utf8'))
    assert old.SCHEMA['properties']['schema_version']['const']=='v1'
    assert latest['properties']['schema_version']['const']=='v1.1'
    with pytest.raises(old.Invalid):old.shape('v1.1',old.SCHEMA['properties']['schema_version'])


def test_identity_claim_depends_on_bridge_and_trusted_verifier():
    s,d,c,v=upstream.fixture(); c['identity']['official_game_id']='wrong'
    with pytest.raises(ValueError): upstream.run(s,d,c,lambda *_:True)
    req={'identity':c['identity'],'decision_at':'2026-10-30T09:30:00+00:00','lineup':None}
    out=upstream.infer(s,req,c,lambda *_:True)
    assert out['identity']['official_game_id']=='wrong'
