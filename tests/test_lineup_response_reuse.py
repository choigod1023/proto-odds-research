from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from isolated_match_responses import IsolatedMatchResponses
from match_api import KST, MatchViews, PreparedMatchResponses, game_key
from match_response_worker import refresh_lineups
from runtime_db import RuntimeDatabase


def serving_fixture(tmp_path):
    now = datetime.now(KST)
    game = dict(year=now.year, round=128, sport='bs', league='KBO',
                date=now.strftime('%m.%d') + ' 23:50', home='홈', away='원정',
                options=[{'selection_id': 'fixed', '배당': 1.8}],
                decision_snapshot={'decision_id': 'frozen', 'probability': {'final': .55}},
                prediction_record={'prediction_snapshot_id': 'frozen', 'result': 'pending'})
    # Exercise KBO details outside recent too; never infer a complete index
    # merely from the three-day list.
    old = dict(game, date=(now-timedelta(days=10)).strftime('%m.%d') + ' 23:50')
    other = dict(game, league='NBA', sport='bk', home='basketball')
    document = {'generated_at': now.isoformat(), 'live': [game, other], 'past': [old],
                'prediction_performance': {'records': {'frozen': {'result': 'pending'}}}}
    kbo = {'generated_at': now.isoformat(), 'games': [{
        'league': 'KBO', 'home_team': '홈', 'away_team': '원정',
        'game_datetime': now.replace(hour=23, minute=50, second=0, microsecond=0).isoformat(),
        'updated_at': now.isoformat(), 'game_id': 'today',
        'starters': {'home': {'name': '처음 투수'}}, 'lineups': {'home': [{'name': '처음 타자'}]}}]}
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('picks_v2', document)
    db.put_document('kbo_pregame', kbo)
    prepared = IsolatedMatchResponses(MatchViews(db), tmp_path / 'responses.zip')
    prepared.requested.add('all')
    prepared.refresh()
    return db, document, kbo, prepared


@pytest.mark.parametrize('change', ['poll', 'lineup', 'remove', 'older_detail'])
def test_lineup_only_matches_full_build_without_reading_prediction_artifact(tmp_path, monkeypatch, change):
    db, document, kbo, previous = serving_fixture(tmp_path)
    original = deepcopy(document)
    stamp = datetime.fromisoformat(kbo['generated_at']) + timedelta(seconds=1)
    kbo['generated_at'] = stamp.isoformat()
    if change == 'lineup':
        kbo['games'][0]['starters']['home']['name'] = '교체 투수'
        kbo['games'][0]['lineups']['home'][0]['name'] = '교체 타자'
        kbo['games'][0]['updated_at'] = stamp.isoformat()
    elif change == 'remove':
        kbo['games'] = []
    elif change == 'older_detail':
        kbo['games'][0]['game_datetime'] = (stamp-timedelta(days=10)).replace(
            hour=23, minute=50, second=0, microsecond=0).isoformat()
    db.put_document('kbo_pregame', kbo)
    baseline = PreparedMatchResponses(MatchViews(db))
    baseline.requested.add('all')
    baseline.refresh()
    # Only serving state may be reused; no expanded prediction read is needed.
    monkeypatch.setattr(db, 'get_artifact_json', lambda *a: pytest.fail('full artifact read'))
    reused = refresh_lineups(db, previous.snapshot_path, ['all', 'performance', 'recent'])
    assert reused is not None
    ready, revisions, revision, games = reused
    for scope in ready:
        import gzip
        assert json.loads(gzip.decompress(ready[scope][1])) == json.loads(
            baseline.get_bytes(scope, compressed=False))
    assert revisions == {scope: revision for scope in ready}
    assert games == baseline.views.games
    other_key = game_key(document['live'][1])
    assert games[other_key] == previous.views.games[other_key]
    assert db.get_artifact('picks_v2') == original


def test_real_child_reuses_snapshot_on_lineup_poll(tmp_path, capfd):
    db, document, kbo, prepared = serving_fixture(tmp_path)
    before = prepared.views.revision
    kbo['generated_at'] = (datetime.fromisoformat(kbo['generated_at']) + timedelta(seconds=1)).isoformat()
    db.put_document('kbo_pregame', kbo)
    prepared.refresh()
    assert 'mode=lineup-only' in capfd.readouterr().out
    assert before != prepared.views.revision
    detail = json.loads(prepared.get_bytes('detail', key=game_key(document['live'][0]),
        revision=prepared.views.revision, compressed=False))
    assert detail['game']['decision_snapshot'] == document['live'][0]['decision_snapshot']


def test_new_prediction_or_new_scope_requires_full_build(tmp_path):
    db, document, kbo, prepared = serving_fixture(tmp_path)
    assert refresh_lineups(db, prepared.snapshot_path, ['missing']) is None
    document['live'][0]['options'][0]['배당'] = 2.0
    db.store_artifact('picks_v2', document)
    assert refresh_lineups(db, prepared.snapshot_path, ['recent']) is None


def test_midnight_and_corrupt_cache_require_full_build(tmp_path, monkeypatch):
    import match_response_worker as worker
    db, document, kbo, prepared = serving_fixture(tmp_path)
    clock = datetime.now(KST)
    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock + timedelta(days=1)
    with monkeypatch.context() as patch:
        patch.setattr(worker, 'datetime', Tomorrow)
        assert refresh_lineups(db, prepared.snapshot_path, ['recent']) is None
    Path(prepared.snapshot_path).write_bytes(b'corrupt')
    assert refresh_lineups(db, prepared.snapshot_path, ['recent']) is None


def test_source_change_during_patch_is_rejected(tmp_path, monkeypatch):
    db, document, kbo, prepared = serving_fixture(tmp_path)
    kbo['generated_at'] = (datetime.fromisoformat(kbo['generated_at']) + timedelta(seconds=1)).isoformat()
    db.put_document('kbo_pregame', kbo)
    monkeypatch.setattr(MatchViews, 'current_revision', lambda self: 'newer-artifact')
    with pytest.raises(RuntimeError, match='revision changed'):
        refresh_lineups(db, prepared.snapshot_path, ['recent'])
