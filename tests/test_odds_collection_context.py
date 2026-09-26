import copy

from src import odds_live
from runtime_db import RuntimeDatabase


def test_projection_preserves_history_and_excludes_unused_fields(tmp_path):
    db = RuntimeDatabase(tmp_path / 'test.sqlite3')
    assert db.odds_collection_context() is None
    picks = {'generated_at': '2026-09-26T00:00:00Z', 'rounds': [114],
             'past': [{'large': 'unused'}], 'live': [
                 {'round': 114, 'players': ['unused'], 'options': [
                     {'게임번호': '1', 'market': '핸디캡', 'label': 'H -1.0', '배당': 1.8, 'extra': 'unused'},
                     {'게임번호': '1', 'market': '핸디캡', 'label': 'H -1.0', '배당': 2.0}]},
                 {'round': '114', 'options': []}, {'round': '114', 'options': None}, {'round': '115'}]}
    db.store_artifact('picks_v2', picks)
    projected = db.odds_collection_context()
    assert projected['rounds'] == picks['rounds']
    assert projected['generated_at'] == picks['generated_at']
    assert 'unused' not in str(projected)
    assert all(g['options'] == [] for g in projected['live'][1:])
    current = {'generated_at': '2026-09-26T00:01:00Z', 'markets': {
        '114': {'1': {'market': '핸디캡', 'label': 'H -1.0', 'odds': [1.9, 1.9]}}}}
    assert odds_live.merge_market_history(copy.deepcopy(current), {}, picks) == odds_live.merge_market_history(copy.deepcopy(current), {}, projected)
    db.store_artifact('picks_v2', {'live': [], 'rounds': []})
    assert db.odds_collection_context()['live'] == []


def test_history_copy_does_not_mutate_previous():
    previous = {'history': {'1': {'2': [{'market': '승패', 'odds': [1.5, 2.5]}]}}}
    snapshot = copy.deepcopy(previous)
    merged = odds_live.merge_market_history({}, previous)
    merged['history']['1']['2'][0]['odds'][0] = 9
    assert previous == snapshot


def test_database_main_uses_projection_and_releases_old_odds(monkeypatch):
    import weakref
    class Document(dict):
        pass
    refs = []
    class DB:
        def odds_collection_context(self):
            return {'rounds': [114], 'live': []}
    def load(name, path):
        assert name == 'live_odds'
        value = Document()
        refs.append(weakref.ref(value))
        return value
    monkeypatch.setattr(odds_live, 'database_enabled', lambda: True)
    monkeypatch.setattr(odds_live, 'RuntimeDatabase', DB)
    monkeypatch.setattr(odds_live, 'load_artifact', load)
    monkeypatch.setattr(odds_live, 'collect', lambda *args: {'rounds': [114], 'n': 1})
    monkeypatch.setattr(odds_live, 'persist_artifact', lambda *args, **kw: None)
    def refresh(data):
        assert refs[0]() is None
    monkeypatch.setattr(odds_live, 'refresh_once', refresh)
    assert odds_live.main([]) == 0
