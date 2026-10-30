"""Offline v1.2 proposal validator. No external receipt verifier is configured."""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = json.loads((Path(__file__).parent / 'schema.json').read_text(encoding='utf-8'))

class Invalid(ValueError):
    pass

def need(condition, message):
    if not condition:
        raise Invalid(message)

def instant(value):
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        need(d.tzinfo is not None and d.utcoffset() is not None, 'timezone required')
        return d
    except (ValueError, TypeError, AttributeError) as exc:
        raise Invalid('invalid timestamp') from exc

def shape(value, spec, path='$'):
    types = spec.get('type')
    if types:
        types = [types] if isinstance(types, str) else types
        checks = {'object': lambda x: isinstance(x, dict), 'array': lambda x: isinstance(x, list),
                  'string': lambda x: isinstance(x, str), 'null': lambda x: x is None,
                  'integer': lambda x: type(x) is int, 'number': lambda x: type(x) in (int, float) and math.isfinite(x)}
        need(any(checks[t](value) for t in types), path + ': type')
    if 'const' in spec: need(type(value) is type(spec['const']) and value == spec['const'], path + ': const')
    if 'enum' in spec: need(value in spec['enum'], path + ': enum')
    if isinstance(value, str):
        if 'minLength' in spec: need(len(value.strip()) >= spec['minLength'], path + ': empty')
        if 'pattern' in spec: need(re.fullmatch(spec['pattern'], value) is not None, path + ': pattern')
        if spec.get('format') == 'date-time': instant(value)
    if type(value) in (int, float):
        if 'minimum' in spec: need(value >= spec['minimum'], path + ': minimum')
        if 'maximum' in spec: need(value <= spec['maximum'], path + ': maximum')
        if 'exclusiveMinimum' in spec: need(value > spec['exclusiveMinimum'], path + ': exclusiveMinimum')
    if isinstance(value, list):
        need(len(value) >= spec.get('minItems', 0), path + ': minItems')
        for i, v in enumerate(value): shape(v, spec['items'], f'{path}[{i}]')
    if isinstance(value, dict):
        need(len(value) >= spec.get('minProperties', 0), path + ': minProperties')
        props = spec.get('properties', {})
        need(set(spec.get('required', [])) <= value.keys(), path + ': missing fields')
        for k, v in value.items():
            rule = props.get(k, spec.get('additionalProperties', True))
            need(rule is not False, path + ': unexpected ' + k)
            if isinstance(rule, dict): shape(v, rule, path + '.' + k)

def content_digest(game, product, selected_aliases, quote, lineups, observations):
    """Hash complete referenced records, including each observation, in reference order."""
    payload = {
        'domain': 'accuracy-structure-v1.2-inputs',
        'game': game,
        'product': product,
        'identity_aliases': selected_aliases,
        'quote': quote,
        'quote_observation': observations[quote['observation_id']],
        'lineups': [{'lineup': lineup, 'observation': observations[lineup['observation_id']]}
                    for lineup in lineups],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()

def digest(freeze):
    payload = {k: v for k, v in freeze.items() if k != 'freeze_hash'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def unique(items, key, name):
    result = {}
    for item in items:
        k = key(item)
        need(k not in result, 'duplicate ' + name)
        result[k] = item
    return result

def _validate(doc, pre_inference=False):
    schema = SCHEMA
    if pre_inference:
        # A new inference has no frozen prediction yet. Retain all identity,
        # product, quote and observation checks without weakening stored records.
        schema = {**SCHEMA, 'properties': {**SCHEMA['properties'],
                  'freezes': {**SCHEMA['properties']['freezes'], 'minItems': 0}}}
    shape(doc, schema)
    if pre_inference:
        need(not doc['freezes'] and not doc['settlements'], 'pre-inference core cannot contain frozen records')
    games = unique(doc['games'], lambda x: x['canonical_game_id'], 'game')
    for g in games.values(): need(g['home_team_id'] != g['away_team_id'], 'same teams')
    aliases = unique(doc['aliases'], lambda x: (x['authority'], x['official_id']), 'official alias')
    for a in aliases.values(): need(a['canonical_game_id'] in games, 'orphan alias')
    need({a['canonical_game_id'] for a in aliases.values()} == set(games), 'missing alias')
    products = unique(doc['products'], lambda x: x['identity']['product_id'], 'product')
    for p in products.values():
        need(p['canonical_game_id'] in games, 'orphan product')
        need(len(set(p['outcomes'])) == len(p['outcomes']), 'duplicate outcome')
        if p['reissue_of'] is not None:
            prev = products.get(p['reissue_of'])
            need(prev is not None and prev is not p and prev['canonical_game_id'] == p['canonical_game_id'], 'invalid reissue')
            need(prev['identity']['market'] == p['identity']['market'] and prev['identity']['period'] == p['identity']['period'] and prev['identity']['line'] == p['identity']['line'] and prev['outcomes'] == p['outcomes'], 'reissue identity mismatch')
            seen = {p['identity']['product_id']}; cursor = prev
            while cursor is not None:
                pid = cursor['identity']['product_id']; need(pid not in seen, 'reissue cycle'); seen.add(pid)
                cursor = products.get(cursor['reissue_of']) if cursor['reissue_of'] is not None else None
    obs = unique(doc['observations'], lambda x: x['observation_id'], 'observation')
    for o in obs.values():
        e = o['evidence']; first = instant(e['first_received_at']); revision = instant(e['revision_received_at'])
        need(first <= revision, 'receipt order')
        if e['published_at_claim'] is not None: need(instant(e['published_at_claim']) <= revision, 'publication claim after revision')
    def product(record):
        p = products.get(record['identity']['product_id'])
        need(p is not None and p['canonical_game_id'] == record['canonical_game_id'] and p['identity'] == record['identity'], 'product/market/period/line mismatch')
        return p
    def observation(record):
        o = obs.get(record['observation_id']); need(o is not None, 'missing observation'); return o['evidence']
    quotes = unique(doc['quotes'], lambda x: x['revision_id'], 'quote revision')
    for q in quotes.values():
        p = product(q); e = observation(q)
        need(set(q['odds']) == set(p['outcomes']), 'incomplete quote')
        need(instant(q['quote_observed_at']) <= instant(e['revision_received_at']), 'quote observed after receipt')
    lineups = unique(doc['lineups'], lambda x: x['revision_id'], 'lineup revision')
    for l in lineups.values():
        g = games.get(l['canonical_game_id']); need(g is not None and l['team_id'] in (g['home_team_id'],g['away_team_id']), 'lineup game/team mismatch')
        observation(l); unique(l['players'], lambda x: x['player_id'], 'player')
        orders = [x['order'] for x in l['players'] if x['order'] is not None]
        need(len(orders) == len(set(orders)), 'duplicate lineup order')
    freezes = unique(doc['freezes'], lambda x: x['freeze_hash'], 'freeze')
    for f in freezes.values():
        p=product(f); g=games[f['canonical_game_id']]; decision=instant(f['decision_at'])
        need(g['status'] == 'scheduled' and decision <= instant(g['kickoff_at']) - timedelta(minutes=30), 'not pregame or cancelled')
        need(set(f['probabilities']) == set(p['outcomes']) and abs(sum(f['probabilities'].values())-1) <= 1e-9, 'probability vector')
        need(f['selection'] in p['outcomes'], 'selection')
        q=quotes.get(f['quote_revision_id']); need(q is not None and q['canonical_game_id']==f['canonical_game_id'] and q['identity']==f['identity'], 'quote mismatch')
        selected_aliases=[]; seen_aliases=set()
        for ref in f['identity_aliases']:
            key=(ref['authority'], ref['official_id'])
            need(key not in seen_aliases, 'duplicate identity alias reference'); seen_aliases.add(key)
            alias=aliases.get(key)
            need(alias is not None and alias['canonical_game_id'] == f['canonical_game_id'], 'identity alias mismatch')
            selected_aliases.append(alias)
        refs=[observation(q)]; selected_lineups=[]
        need(len(set(f['lineup_revision_ids'])) == len(f['lineup_revision_ids']), 'duplicate lineup reference')
        for rid in f['lineup_revision_ids']:
            l=lineups.get(rid); need(l is not None and l['canonical_game_id']==f['canonical_game_id'], 'lineup mismatch'); refs.append(observation(l)); selected_lineups.append(l)
        for e in refs:
            need(e['availability_tier']=='observed' and instant(e['revision_received_at']) <= decision, 'unavailable at decision')
        need(content_digest(g, p, selected_aliases, q, selected_lineups, obs) == f['input_content_sha256'], 'input content hash mismatch')
        need(digest(f)==f['freeze_hash'], 'freeze hash mismatch')
    unique(doc['settlements'], lambda x: x['freeze_hash'], 'settlement')
    for s in doc['settlements']:
        product(s); f=freezes.get(s['freeze_hash']); need(f is not None and f['canonical_game_id']==s['canonical_game_id'] and f['identity']==s['identity'], 'settlement mismatch')
        e=observation(s); final=instant(s['final_at'])
        need(final >= instant(f['decision_at']) and final <= instant(e['revision_received_at']), 'result timing')
        if s['status'] == 'final': need(final >= instant(games[s['canonical_game_id']]['kickoff_at']), 'result before kickoff')
        need(s['settlement_scope'] == s['identity']['period'], 'settlement scope mismatch')
        need((s['status']=='void' and s['score'] is None) or (s['status']=='final' and s['score'] is not None), 'result status/score')
    return True

def validate(doc):
    """Validate a stored v1.2 document, including at least one frozen prediction."""
    return _validate(doc)


def validate_inference_core(doc):
    """Validate an unfrozen input projection; no receipt or freeze authentication."""
    return _validate(doc, pre_inference=True)


def validate_append(before, after):
    validate(before); validate(after)
    for key in before:
        if isinstance(before[key], list):
            need(after[key][:len(before[key])] == before[key], 'history changed: ' + key)
        else: need(after[key] == before[key], 'version changed')
    return True
