import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "experiments" / "accuracy-development-20261030"
sys.path.insert(0, str(DIR))
from adapter import infer
from baseline_model import State


def request():
    return {"identity": {"league": "KBO", "official_game_id": "g1", "product_id": "full-win", "home_team_id": "H", "away_team_id": "A", "kickoff_at": "2026-10-30T19:00:00+09:00"}, "decision_at": "2026-10-30T18:00:00+09:00", "lineup": None}


def lineup():
    return {"official_game_id": "g1", "source_published_at_claim": "2026-10-30T17:00:00+09:00", "first_received_at": "2026-10-30T17:10:00+09:00", "revision_received_at": "2026-10-30T17:20:00+09:00", "source_url": "https://example.org/lineup", "raw_sha256": "a"*64, "availability_tier": "unverified", "external_receipt_verified": False, "sport": "baseball", "players": [{"team_id": team, "player_id": team+role, "role": role, "order": None, "announcement_status": status, "expected_minutes_or_innings": amount} for team in ("H", "A") for role, status, amount in (("starter", "probable", 6), ("bullpen", "confirmed", 3))]}


def test_baseline_preserved_and_serialization():
    state = State("KBO", "outcome", 0)
    req = request(); before = state.export()
    expected, _ = state.predict("H", "A", __import__('datetime').datetime.fromisoformat(req['decision_at']))
    absent = infer(state, req)
    req['lineup'] = lineup(); present = infer(state, req)
    assert absent['probability_vector'] == present['probability_vector'] == expected
    assert absent['availability_reason'] == 'lineup_unavailable'
    assert present['availability_reason'] == 'player_effects_not_fitted'
    assert state.export() == before
    restored = State.restore(json.loads(json.dumps(present))['baseline_state'])
    assert infer(restored, request())['probability_vector'] == expected
    h = present.pop('freeze_hash')
    canonical = json.dumps(present, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert h == hashlib.sha256(canonical.encode()).hexdigest()
    req['lineup']['players'][0]['player_id'] = 'changed'
    assert present['input_revision']['players'][0]['player_id'] == 'Hstarter'


@pytest.mark.parametrize('change', [
    lambda r: r['lineup'].update(official_game_id='other'),
    lambda r: r['lineup'].update(first_received_at='2026-10-30T18:01:00+09:00'),
    lambda r: r['lineup'].update(revision_received_at='2026-10-30T18:01:00+09:00'),
    lambda r: r['lineup'].update(source_published_at_claim='2026-10-30T17:00:00'),
    lambda r: r['lineup'].update(external_receipt_verified=True),
    lambda r: r['lineup'].update(score=[1, 0]),
    lambda r: r.update(result={'score': [1, 0]}),
    lambda r: r['lineup']['players'][0].update(announcement_status='unknown'),
    lambda r: r['lineup']['players'][0].update(expected_minutes_or_innings=7),
    lambda r: r['lineup']['players'][0].update(expected_minutes_or_innings=None),
    lambda r: r['lineup']['players'][0].update(player_id='Abullpen'),
    lambda r: r['lineup']['players'][0].update(current_score=5),
    lambda r: r['identity'].update(kickoff_at='2026-10-30T18:00:00+09:00'),
])
def test_reject_invalid_or_leaky_input(change):
    r = request(); r['lineup'] = lineup(); change(r)
    with pytest.raises(ValueError): infer(State('KBO', 'outcome', 0), r)


def test_soccer_minutes():
    r = request(); r['identity']['league'] = 'K리그1'; l = lineup(); l['sport'] = 'soccer'
    l['players'] = [dict(team_id=t, player_id=f'{t}{i}', role='player', order=i, announcement_status='confirmed', expected_minutes_or_innings=90) for t in ('H', 'A') for i in range(1, 12)]
    r['lineup'] = l
    assert len(infer(State('K리그1', 'outcome', 0), r)['probability_vector']) == 3
    l['players'][0]['expected_minutes_or_innings'] = 89
    with pytest.raises(ValueError): infer(State('K리그1', 'outcome', 0), r)
