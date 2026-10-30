import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'experiments/accuracy-connection-20261030'
def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf8'))

def test_official_history_identity_and_integrity():
    rows = load('official-2025.json')
    ids = [r['official_game_id'] for r in rows]
    assert len(ids) == len(set(ids)) == 275
    assert all(r['league'] == 'K리그2' and r['kickoff_at'].startswith('2025-') for r in rows)
    assert all(len(r['score']) == 2 and all(isinstance(s, int) and s >= 0 for s in r['score']) for r in rows)
    assert all(r['home_team_id'] != r['away_team_id'] and datetime.fromisoformat(r['first_received_at']) > datetime.fromisoformat(r['kickoff_at']) for r in rows)
    links = load('links.json')
    assert len(links['links']) == 23 and not links['unmatched']
    assert len({r['official_game_id'] for r in links['links']}) == 23
    assert {r['official_game_id'] for r in links['links']} <= set(ids)

def test_probes_do_not_claim_certified_lineups():
    rows = load('upcoming-probes.json')
    assert len({(x['league'], x['official_game_id']) for x in rows}) == len(rows)
    for r in rows:
        assert datetime.fromisoformat(r['first_received_at']) < datetime.fromisoformat(r['kickoff_at'])
        assert r['external_receipt_verified'] is False
        assert r['lineup'] is None and r['product_id'] is None
        assert len(r['raw_sha256']) == 64
    requests = load('requests.json')
    assert len(requests) == 21
    assert all(len(x['raw_sha256']) == 64 for x in requests)
