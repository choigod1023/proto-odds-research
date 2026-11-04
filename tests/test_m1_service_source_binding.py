"""Synthetic regression counterexamples; never source-authenticated performance."""
import base64
from copy import deepcopy
import gzip
import json

import pytest

from research.m1_service.observer import _verify_archives, canonical, digest, moment


def source_fixture(tmp_path, monkeypatch):
    from research.kbo_forward import official, features
    from research.kbo_forward.test_features import data, NOW
    event, roster, history = data(); mapping = {}
    for side, offset in [('home', 10000), ('away', 20000)]:
        old = deepcopy(roster[side])
        roster[side] = {'starter': str(offset), 'hitters': [str(offset+i+1) for i in range(9)]}
        mapping[old['starter']] = roster[side]['starter']
        mapping.update(zip(old['hitters'], roster[side]['hitters']))
    for g in history['seed']['games']+history['games']:
        for role in ('batters', 'pitchers'):
            g[role] = {mapping.get(k, k): v for k, v in g[role].items()}
    root = tmp_path/'official'; (root/'bodies').mkdir(parents=True); (root/'verified-bundles').mkdir()
    game = {'G_ID': event['official_game_id'], 'G_DT': '20261104', 'G_TM': '18:30',
            'HOME_ID': event['home_id'], 'AWAY_ID': event['away_id'], 'SEASON_ID': '2026',
            'HOME_NM': '삼성', 'AWAY_NM': 'KIA', 'LE_ID': '1', 'SR_ID': '7',
            'START_PIT_CK': True, 'B_PIT_P_ID': roster['home']['starter'], 'T_PIT_P_ID': roster['away']['starter'],
            'B_PIT_P_NM': 'HomePitcher', 'T_PIT_P_NM': 'AwayPitcher', 'GAME_STATE_SC': '1', 'CANCEL_SC_ID': '0'}
    def meta(side):
        return {'G_ID': game['G_ID'], 'T_ID': event[side+'_id'], 'LE_ID': '1', 'SR_ID': '7', 'SEASON_ID': '2026'}
    def players(side):
        return json.dumps({'rows': [{'row': [{'Text': str(i+1)}, {'Text': 'C'}, {'Text': 'name'+p}]} for i, p in enumerate(roster[side]['hitters'])]})
    lineup = [[{'LINEUP_CK': True}], [meta('home')], [meta('away')], [players('home')], [players('away')]]
    def response(url, body, params):
        raw = canonical(body); sha = digest(raw); (root/'bodies'/(sha+'.bin')).write_bytes(raw)
        rec = {'url': url, 'params': params, 'requested_at': NOW, 'finished_at': NOW, 'status': 200,
               'sha256': sha, 'body_file': 'bodies/'+sha+'.bin'}
        return {'receipt': rec, 'body_base64': base64.b64encode(raw).decode('ascii')}
    schedule = response(official.SCHEDULE, {'game': [game]}, {'date': '20261104'})
    ann = {'responses': [schedule, response(official.LINEUP, lineup, {'gameId': game['G_ID']})]}
    hist = {'responses': [schedule], 'normalized_history': history}
    evidence = {}
    for name, body in [('announcement', ann), ('history', hist)]:
        raw = canonical(body); sha = digest(raw); path = root/'verified-bundles'/(sha+'.json.gz')
        path.write_bytes(gzip.compress(raw, mtime=0))
        wrapper = {'archived_canonical_bundle_sha256': sha, 'relative_file': str(path.relative_to(root))}
        evidence[name] = {'body': wrapper, 'sha256': digest(canonical(wrapper)), 'received_at': NOW}
    payload = {'event': event, 'row': features.build_row(event, roster, history, NOW), 'evidence': evidence}
    # Isolate binding composition here: the official source client's raw
    # box-score/seed and player identity verifiers have independent tests.
    monkeypatch.setattr(official.OfficialClient, 'verify_history_stats', lambda *args: True)
    monkeypatch.setattr(official.OfficialClient, 'resolve_identity', lambda self, name, *a, **kw: (name.removeprefix('name'), []))
    return payload, root, moment(NOW)


def test_row_is_reconstructed_from_same_archived_history(tmp_path, monkeypatch):
    payload, root, now = source_fixture(tmp_path, monkeypatch)
    _verify_archives(payload, root, now)
    edited = deepcopy(payload)
    edited['row']['raw_features']['home_rest_days']['value'] = 10
    edited['row']['features']['home_rest_days']['value'] = 10.
    with pytest.raises(ValueError, match='differs from archived history'):
        _verify_archives(edited, root, now)


def test_plain_metadata_and_announced_roster_tamper_are_rejected(tmp_path, monkeypatch):
    payload, root, now = source_fixture(tmp_path, monkeypatch)
    edited = deepcopy(payload); edited['evidence']['history']['body'] = {'caller_verified': True}
    with pytest.raises(ValueError, match='raw source bundle'):
        _verify_archives(edited, root, now)
    edited = deepcopy(payload); edited['row']['roster']['home']['hitters'][0] = '99999'
    with pytest.raises(ValueError, match='ordered hitter IDs'):
        _verify_archives(edited, root, now)
