"""Explicit v1.2 new-inference bridge; no freeze or receipt authentication."""
from copy import deepcopy
from .adapter import infer


def infer_v1(state, doc, game_id, quote_revision_id, lineup_revision_ids, decision_at, contract, validate_v1, verify_contract):
    """Validate a quote-only v1.2 projection, bind it to an independently verified contract.

    Lineup revisions are optional and are never required for core validation.
    No existing v1 freeze or settlement is read as a feature or authenticated.
    """
    if not callable(validate_v1):
        raise ValueError("v1 validator required")
    def one(items, key, value):
        matches = [x for x in items if isinstance(x, dict) and x.get(key) == value]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous " + key)
        return matches[0]
    g = one(doc['games'], 'canonical_game_id', game_id)
    q = one(doc['quotes'], 'revision_id', quote_revision_id)
    product = one(doc['products'], 'identity', q['identity'])
    evidence = one(doc['observations'], 'observation_id', q['observation_id'])['evidence']
    aliases = [a for a in doc['aliases'] if a.get('canonical_game_id') == game_id and a.get('authority') == 'official']
    if len(aliases) != 1 or q['canonical_game_id'] != game_id or product['canonical_game_id'] != game_id or g['status'] != 'scheduled':
        raise ValueError('ambiguous game or product')
    # Validator sees a minimal core projection; malformed or late lineups cannot
    # suppress a safe baseline. Original freeze hashes are deliberately not used.
    core = {'schema_version': doc['schema_version'], 'games': [g], 'aliases': aliases,
            'products': [product], 'observations': [one(doc['observations'], 'observation_id', q['observation_id'])],
            'quotes': [q], 'lineups': [], 'freezes': [], 'settlements': []}
    if validate_v1(deepcopy(core)) is not True:
        raise ValueError('v1 core validation failed')
    ident = {'league': g['league'], 'canonical_game_id': game_id, 'official_game_id': aliases[0]['official_id'], 'product_id': q['identity']['product_id'],
             'home_team_id': g['home_team_id'], 'away_team_id': g['away_team_id'], 'kickoff_at': g['kickoff_at']}
    p = dict(q['identity']); p['outcomes'] = product['outcomes']
    quote = {'product_id': p['product_id'], 'observed_at': q['quote_observed_at'],
             'first_received_at': evidence['first_received_at'], 'revision_received_at': evidence['revision_received_at'],
             'availability_tier': evidence['availability_tier'], 'odds': q['odds'],
             'source_url': evidence['source_url'], 'source_sha256': evidence['source_sha256'],
             'published_at_claim': evidence['published_at_claim'], 'external_receipt_verified': evidence['external_receipt_verified']}
    if contract.get('identity') != ident or contract.get('product') != p or contract.get('quote') != quote:
        raise ValueError('verified contract does not match v1 core')
    lineup = None
    if lineup_revision_ids:
        try:
            if not isinstance(lineup_revision_ids, list) or len(set(lineup_revision_ids)) != len(lineup_revision_ids):
                raise ValueError('invalid lineup revision references')
            rows = [one(doc['lineups'], 'revision_id', rid) for rid in lineup_revision_ids]
            if any(set(row) != {'revision_id', 'canonical_game_id', 'team_id', 'observation_id', 'players'} for row in rows):
                raise ValueError('unexpected lineup fields')
            player_keys = {'player_id', 'role', 'order', 'announcement_status', 'expected_minutes_or_innings'}
            if any(not isinstance(player, dict) or set(player) != player_keys for row in rows for player in row['players']):
                raise ValueError('unexpected player fields')
            if any(row['canonical_game_id'] != game_id for row in rows):
                raise ValueError('lineup game mismatch')
            observation_ids = {row['observation_id'] for row in rows}
            if len(observation_ids) != 1:
                raise ValueError('multiple lineup observations unsupported')
            ev = one(doc['observations'], 'observation_id', observation_ids.pop())['evidence']
            players = [dict(player, team_id=row['team_id']) for row in rows for player in row['players']]
            lineup = {'official_game_id': ident['official_game_id'], 'source_published_at_claim': ev['published_at_claim'],
                      'first_received_at': ev['first_received_at'], 'revision_received_at': ev['revision_received_at'],
                      'source_url': ev['source_url'], 'raw_sha256': ev['source_sha256'],
                      'availability_tier': ev['availability_tier'], 'external_receipt_verified': ev['external_receipt_verified'],
                      'sport': 'soccer' if state.soccer else 'baseball', 'players': players}
        except (KeyError, TypeError, ValueError):
            lineup = {}  # Invalid player input; infer discards it and reports why.
    return infer(state, {'identity': ident, 'decision_at': decision_at, 'lineup': lineup}, contract, verify_contract)
