from datetime import datetime, timezone
from copy import deepcopy
import requests
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from kbo_pregame import collect, matching_record
from baseball_context_watch import normalize_lineup
from test_live_market_refresh import _live_odds
from live_market_refresh import refresh_document

NOW = datetime(2026, 8, 30, 1, tzinfo=timezone.utc)
GAME = {"gameId": "test", "statusCode": "BEFORE", "homeTeamName": "KIA",
        "awayTeamName": "SSG", "gameDateTime": "2026-08-30T18:00:00",
        "homeStarterName": "Home", "awayStarterName": "Away"}
BATTERS = [{"playerCode": str(i), "playerName": f"B{i}", "batOrder": i,
            "positionName": "타자"} for i in range(1, 10)]


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class Session:
    def __init__(self, failed=False, announced=False):
        self.failed, self.announced = failed, announced
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith('/preview'):
            if self.failed: raise requests.Timeout()
            return Response({"result": {"previewData": {
                "homeTeamLineUp": {"batterCandidate": BATTERS, "fullLineUp": []}}}})
        if url.endswith('/lineup'):
            return Response({"result": {"lineUpData": {
                "home": {"fullLineUp": BATTERS}, "away": {"fullLineUp": BATTERS}
            } if self.announced else None}})
        return Response({"result": {"games": [GAME]}})


def test_candidates_are_not_announced_batting_order():
    assert normalize_lineup({"fullLineUp": [dict(p, batOrder=None, batorder=p['batOrder']) for p in BATTERS]})['confirmed']
    assert not normalize_lineup({"batterCandidate": BATTERS, "fullLineUp": []})['confirmed']
    assert normalize_lineup({"fullLineUp": BATTERS})['confirmed']
    duplicates = [dict(p, batOrder=1) for p in BATTERS]
    assert not normalize_lineup(duplicates)['confirmed']


def test_unannounced_and_failures_are_distinct_and_retry_independent():
    doc = collect(Session(), NOW, {})
    row = doc['games'][0]
    assert row['starters']['home']['name'] == 'Home'
    assert row['lineups'] == {}
    assert row['lineup_status']['side_states']['home'] == 'not_announced'
    session = Session(failed=True, announced=True)
    row = collect(session, NOW, {})['games'][0]
    assert row['lineup_status']['official_today']
    assert len(row['lineups']['home']) == 9
    assert any(u.endswith('/lineup') for u in session.calls)


def test_failed_refresh_retains_last_lineup_timestamp_as_stale():
    previous = collect(Session(announced=True), NOW, {})
    doc = collect(Session(failed=True), NOW, previous)
    row = doc['games'][0]
    assert row['lineups'] == previous['games'][0]['lineups']
    assert row['lineup_status']['side_states']['home'] == 'stale'
    assert not row['lineup_status']['official_today']


def test_context_change_without_odds_change_rebuilds_unfrozen_decision_only():
    document, _ = refresh_document({'live': []}, _live_odds())
    doc = collect(Session(announced=True), NOW, {})
    refreshed, changed = refresh_document(document, _live_odds(), now=NOW, player_document=doc)
    assert changed == 1
    game = refreshed['live'][0]
    assert game['선발']['home'] == 'Home'
    assert game['research_player_inputs']['status'] == 'captured'
    assert game['research_player_inputs']['affects_probability'] is False
    frozen = deepcopy(refreshed)
    doc['games'][0]['starters']['home']['name'] = 'Changed'
    refreshed, changed = refresh_document(refreshed, _live_odds(),
        now=datetime(2026,8,30,8,35,tzinfo=timezone.utc), player_document=doc)
    assert changed == 0
    assert refreshed == frozen


def test_display_rejects_wrong_day_and_doubleheader():
    doc = collect(Session(), NOW, {})
    game = dict(league='KBO', home='KIA', away='SSG', date='08.30(일) 18:00')
    assert matching_record(game, doc, NOW)
    assert not matching_record(dict(game, date='08.31(월) 18:00'), doc, NOW)
    doc['games'].append(deepcopy(doc['games'][0]))
    assert not matching_record(game, doc, NOW)


def test_view_lineup_revision_changes_without_mutating_frozen_artifact(tmp_path, monkeypatch):
    from runtime_db import RuntimeDatabase
    from match_api import MatchViews, game_key
    import kbo_pregame
    original_match = kbo_pregame.matching_record
    monkeypatch.setattr(kbo_pregame, 'matching_record', lambda g,d: original_match(g,d,NOW))
    db = RuntimeDatabase(tmp_path/'test.db')
    game = dict(year=2026, sport='bs', league='KBO', home='KIA', away='SSG',
                date='08.30(일) 18:00', round=102, options=[], decision_snapshot={'frozen':True})
    db.store_artifact('picks_v2', {'live':[game]})
    views = MatchViews(db)
    before = views.get('all')['view']['revision']
    db.put_document('kbo_pregame', collect(Session(announced=True), NOW, {}))
    after = views.get('all')['view']['revision']
    assert before != after
    detail = views.get('detail', game_key(game), after)['game']
    assert detail['baseball_current']['home'] == 'Home'
    assert detail['decision_snapshot'] == game['decision_snapshot']
    assert 'baseball_current' not in db.get_artifact('picks_v2')['live'][0]
