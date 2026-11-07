import copy,sys,importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).parent))
# Root pytest may already have imported src/features.py. Load this test's
# sibling explicitly, without replacing that unrelated module for other tests.
_spec=importlib.util.spec_from_file_location("kbo_forward_connected_test_features",Path(__file__).with_name("features.py"))
_features=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_features)
with patch.dict(sys.modules,{"features":_features}):
    import connected
from test_ledger import payload

def test_unannounced_game_outside_prewarm_window_does_not_fetch_history(tmp_path):
    client=connected.ConnectedOfficial(tmp_path)
    observed_at = datetime(2026, 11, 5, 18, 1, tzinfo=connected.KST)
    with (
        patch.object(connected, 'now_utc', return_value=observed_at),
        patch.object(connected.OfficialClient, 'snapshot', return_value={
            'source_verified': False, 'announcement_verified': False,
            'missing': ['confirmed_lineups'],
        }),
        patch.object(client, 'prepare_history') as prepare_history,
    ):
        assert not client.snapshot({'source':{'G_DT':'20261105','G_TM':'18:30'}})['source_verified']
    prepare_history.assert_not_called()

def test_verified_source_bundles_are_preserved_and_tamper_is_detected(tmp_path):
    client=connected.ConnectedOfficial(tmp_path);x=payload();x.pop('market');x['evidence'].pop('odds');x['row']['history_coverage']={k:{'status':'complete'} for k in ('home','away')}
    answer={'source_verified':True,'payload':x,'row':x['row']}
    answer['announcement_verified'] = True
    def verify_once(game, history, build_row):
        assert history() == {'test_only': True}
        assert build_row is connected.build_row
        return answer
    with (patch.object(connected.OfficialClient, 'snapshot', side_effect=verify_once) as snapshot,
          patch.object(client, 'prepare_history', return_value={'test_only': True}) as prepare):
        actual = client.snapshot({'source': {'G_DT': '20261105', 'G_ID': 'game'}})
    assert snapshot.call_count == prepare.call_count == 1
    connected.verify_archived_bundles(actual['payload'],tmp_path)
    ref=actual['payload']['evidence']['history']['body']
    assert ref['archived_canonical_bundle_sha256'] and ref['contains_direct_response_bytes_and_receipts']
    assert not actual['payload']['row']['input_quality']['missing_features']==['invented']
    path=tmp_path/ref['relative_file'];path.write_bytes(b'corrupted')
    with pytest.raises(Exception):connected.verify_archived_bundles(actual['payload'],tmp_path)


def test_prewarm_is_one_current_day_uncached_game_before_capture_window(tmp_path):
    client = connected.ConnectedOfficial(tmp_path)
    now = datetime(2026, 11, 5, 14, 0, tzinfo=connected.KST)
    def fixture(game_id, kickoff, day='20261105', state='1', cancelled='0'):
        return {'source': {'G_DT': day, 'G_TM': kickoff, 'G_ID': game_id, 'HOME_ID': 'SS',
                           'AWAY_ID': 'KT', 'GAME_STATE_SC': state, 'CANCEL_SC_ID': cancelled}}

    cached = fixture('cached', '18:30')
    cached['source']['AWAY_ID'] = 'LT'
    _, path = client._history_index(cached['source'])
    path.parent.mkdir()
    path.touch()
    a, b = fixture('a', '18:30'), fixture('b', '19:00')
    with patch.object(client, 'prepare_history') as prepare:
        client.prewarm_history([a, b, cached, fixture('close', '16:30'),
                                fixture('late', '20:30'), fixture('cancelled', '18:30', cancelled='1'),
                                fixture('tomorrow', '18:30', day='20261106')], now)
        assert prepare.call_count == 1
        assert prepare.call_args.args[0] in (a, b)
