import copy,sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).parent))
import connected
from test_ledger import payload

def test_unannounced_game_never_materializes_or_fetches_history(tmp_path):
    client=connected.ConnectedOfficial(tmp_path)
    with patch.object(connected.OfficialClient,'snapshot',return_value={'source_verified':False,'announcement_verified':False,'missing':['confirmed_lineups']}),patch.object(connected,'seed_from_frozen',side_effect=AssertionError('unnecessary history work')):
        assert not client.snapshot({'source':{}})['source_verified']

def test_verified_source_bundles_are_preserved_and_tamper_is_detected(tmp_path):
    client=connected.ConnectedOfficial(tmp_path);x=payload();x.pop('market');x['evidence'].pop('odds');x['row']['history_coverage']={k:{'status':'complete'} for k in ('home','away')}
    answer={'source_verified':True,'payload':x,'row':x['row']}
    with patch.object(connected.OfficialClient,'snapshot',side_effect=[{'announcement_verified':True},answer]),patch.object(connected,'seed_from_frozen',return_value={'test_only':True}),patch.object(client,'make_history',return_value={'test_only':True}):
        actual=client.snapshot({'source':{'G_DT':'20261105'}})
    connected.verify_archived_bundles(actual['payload'],tmp_path)
    ref=actual['payload']['evidence']['history']['body']
    assert ref['archived_canonical_bundle_sha256'] and ref['contains_direct_response_bytes_and_receipts']
    assert not actual['payload']['row']['input_quality']['missing_features']==['invented']
    path=tmp_path/ref['relative_file'];path.write_bytes(b'corrupted')
    with pytest.raises(Exception):connected.verify_archived_bundles(actual['payload'],tmp_path)
