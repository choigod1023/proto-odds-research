from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import player_info


def response(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


def test_injury_observation_is_per_successful_team_not_global_cache():
    def get(url, **kwargs):
        if '/2/' in url:
            raise requests.Timeout()
        return response({'roster': [{'person': {'fullName': 'Player'},
            'status': {'code': 'D10', 'description': 'Injured 10-Day'}}]})
    before = datetime.now(timezone.utc).replace(microsecond=0)
    rows = player_info._mlb_injuries(SimpleNamespace(get=get), {1, 2})
    after = datetime.now(timezone.utc)
    assert set(rows) == {'1'}
    player = rows['1'][0]
    assert before <= datetime.fromisoformat(player['observed_at']) <= after
    assert player['reason_code'] == 'injury'
    assert player['source_url'].endswith('/1/roster')


def test_failed_team_cache_keeps_old_observation_and_lineup_has_side_status(monkeypatch):
    old = '2026-01-01T00:00:00+00:00'
    existing = {'team_injuries': {'2': [{'name': 'Cached', 'status': 'out',
                                      'source_type': 'official_roster', 'observed_at': old}]}}
    now = datetime.now(timezone.utc).isoformat()
    game = {'gamePk': 123, 'gameDate': now,
            'teams': {'home': {'team': {'id': 1, 'name': 'Home'}},
                      'away': {'team': {'id': 2, 'name': 'Away'}}},
            'lineups': {'homePlayers': [{'fullName': f'B{i}'} for i in range(9)]}}
    session = SimpleNamespace(get=lambda *a, **k: response({'dates': [{'games': [game]}]}))
    monkeypatch.setattr(player_info, '_mlb_pitcher_stats', lambda *a: {})
    monkeypatch.setattr(player_info, '_cached_injuries', lambda *a: ({}, False))
    monkeypatch.setattr(player_info, '_mlb_injuries', lambda *a: {'1': []})
    games, injuries, refreshed = player_info.mlb_games(existing, session)
    assert refreshed != old
    assert injuries['2'][0]['observed_at'] == old
    assert games[0]['unavailable']['away'][0]['observed_at'] == old
    assert games[0]['lineup_status']['side_states'] == {'home': 'official_today', 'away': 'pending'}
    assert games[0]['lineup_status']['state'] == 'partial'
