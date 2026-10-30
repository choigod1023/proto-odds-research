import json
import pytest
import hashlib
import importlib.util
from copy import deepcopy
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


def test_identity_precedes_score_and_reconciles_separately():
    spec = importlib.util.spec_from_file_location('connection_match', ROOT / 'match.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prior, official = load('prior-2025.json'), load('official-2025.json')
    saved = load('links.json')
    actual = module.link(prior, official)
    assert actual['links'] == saved['links'] and actual['unmatched'] == saved['unmatched']
    assert all(x['reconciliation']['status'] == 'agree' for x in actual['links'])
    for name, filename in [('prior_derived_lf_bytes', 'prior-2025.json'), ('official_derived_lf_bytes', 'official-2025.json')]:
        assert hashlib.sha256((ROOT / filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == saved['input_sha256'][name]
    poisoned = deepcopy(prior)
    for row in poisoned:
        row['hg'], row['ag'] = 999, 998
    poisoned_links = module.link(poisoned, official)
    assert [x['official_game_id'] for x in poisoned_links['links']] == [x['official_game_id'] for x in actual['links']]
    assert all(x['reconciliation']['status'] == 'disagree' for x in poisoned_links['links'])
    poisoned_official = deepcopy(official)
    for row in poisoned_official:
        row['score'] = [997, 996]
    assert [x['official_game_id'] for x in module.link(prior, poisoned_official)['links']] == [x['official_game_id'] for x in actual['links']]
    duplicate = deepcopy(next(r for r in official if r['official_game_id'] == actual['links'][0]['official_game_id']))
    duplicate['official_game_id'] = 'duplicate-game'
    duplicate['score'] = [999, 998]
    ambiguous = module.link(poisoned, official + [duplicate])
    assert ambiguous['unmatched'][0]['reason'] == 'ambiguous'
    assert ambiguous['unmatched'][0]['candidate_count'] == 2
    assert actual['links'][0]['official_game_id'] not in {x['official_game_id'] for x in ambiguous['links']}


def test_duplicate_prior_identity_is_ambiguous():
    spec = importlib.util.spec_from_file_location('connection_match_duplicate', ROOT / 'match.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prior, official = load('prior-2025.json'), load('official-2025.json')
    result = module.link(prior + [deepcopy(prior[0])], official)
    assert len(result['links']) == 22
    assert len(result['unmatched']) == 2
    assert all(x['reason'] == 'ambiguous' for x in result['unmatched'])


def test_duplicate_official_id_across_different_keys_fails_closed_even_with_poisoned_scores():
    spec = importlib.util.spec_from_file_location('connection_match_duplicate_id', ROOT / 'match.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prior, official = load('prior-2025.json'), load('official-2025.json')
    saved = load('links.json')['links']
    first = next(r for r in official if r['official_game_id'] == saved[0]['official_game_id'])
    second = next(r for r in official if r['official_game_id'] == saved[1]['official_game_id'])
    assert module.key_official(first) != module.key_official(second)
    second['official_game_id'] = first['official_game_id']
    for row in prior:
        row['hg'], row['ag'] = 999, 998
    for row in official:
        row['score'] = [999, 998]
    with pytest.raises(ValueError, match='duplicate official game ID'):
        module.link(prior, official)


def test_missing_official_id_fails_closed():
    spec = importlib.util.spec_from_file_location('connection_match_missing_id', ROOT / 'match.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    official = load('official-2025.json')
    official[0]['official_game_id'] = ''
    with pytest.raises(ValueError, match='missing official game ID'):
        module.link(load('prior-2025.json'), official)
