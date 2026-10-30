"""Exercise current worker modules together, not archived review vendor copies."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / 'experiments/accuracy-structure-20261030'
D = ROOT / 'experiments/accuracy-development-20261030'
spec = importlib.util.spec_from_file_location('integrated_structure', S / 'validator.py')
structure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(structure)
package_spec = importlib.util.spec_from_file_location('integrated_development', D / '__init__.py',
                                                    submodule_search_locations=[str(D)])
package = importlib.util.module_from_spec(package_spec)
sys.modules[package_spec.name] = package
package_spec.loader.exec_module(package)
from integrated_development.adapter import _canonical
from integrated_development.baseline_model import State
from integrated_development.bridge import infer_v1


def inputs():
    doc = json.loads((S / 'fixture-pregame.json').read_text(encoding='utf-8'))
    g, p, q = doc['games'][0], doc['products'][0], doc['quotes'][0]
    ev = next(o['evidence'] for o in doc['observations'] if o['observation_id'] == q['observation_id'])
    alias = next(a for a in doc['aliases'] if a['authority'] == 'official')
    decision = doc['freezes'][0]['decision_at']
    state = State(g['league'], 'outcome', 0)
    state.update({'date': '2026-10-01', 'league': g['league'], 'home': 'Home Name', 'away': 'Away Name', 'hg': 3, 'ag': 1},
                 datetime(2026, 10, 5, tzinfo=timezone.utc))
    ident = dict(league=g['league'], canonical_game_id=g['canonical_game_id'], official_game_id=alias['official_id'],
                 product_id=q['identity']['product_id'], home_team_id=g['home_team_id'],
                 away_team_id=g['away_team_id'], kickoff_at=g['kickoff_at'])
    product = dict(q['identity'], outcomes=p['outcomes'])
    quote = dict(product_id=ident['product_id'], observed_at=q['quote_observed_at'],
                 first_received_at=ev['first_received_at'], revision_received_at=ev['revision_received_at'],
                 availability_tier=ev['availability_tier'], odds=q['odds'], source_url=ev['source_url'],
                 source_sha256=ev['source_sha256'], published_at_claim=ev['published_at_claim'],
                 external_receipt_verified=ev['external_receipt_verified'])
    contract = dict(identity=ident, product=product, decision_at=decision,
                    team_mapping={g['home_team_id']: 'Home Name', g['away_team_id']: 'Away Name'},
                    state_sha256=hashlib.sha256(_canonical(state.export()).encode('utf-8')).hexdigest(),
                    state_available_through=state.clock.isoformat(), quote=quote)
    trusted_contract, trusted_state = copy.deepcopy(contract), copy.deepcopy(state.export())
    # Synthetic fixture authority only: this does not implement a production witness.
    def verify(candidate, baseline):
        return candidate == trusted_contract and baseline == trusted_state
    return doc, state, contract, verify


def call(doc, state, contract, verify, lineups=None):
    return infer_v1(state, doc, doc['games'][0]['canonical_game_id'], doc['quotes'][0]['revision_id'],
                    lineups or [], contract['decision_at'], contract, structure.validate_inference_core, verify)


def test_current_v12_structure_and_adapter_preserve_baseline_without_player_data():
    doc, state, contract, verify = inputs()
    assert doc['schema_version'] == 'v1.2' and structure.validate(doc)
    before = copy.deepcopy(state.export())
    output = call(doc, state, contract, verify)
    assert output['probability_vector'] == state.predict('Home Name', 'Away Name', datetime.fromisoformat(contract['decision_at']))[0]
    assert output['availability_reason']['code'] == 'lineup_unavailable'
    assert state.export() == before
    partial = call(doc, state, contract, verify, [row['revision_id'] for row in doc['lineups']])
    assert partial['probability_vector'] == output['probability_vector']
    assert partial['availability_reason']['code'] == 'lineup_invalid'


@pytest.mark.parametrize('change', ['kickoff', 'official_id', 'quote', 'observation', 'lineup'])
def test_current_content_binding_detects_changes_without_rehash(change):
    doc, _, _, _ = inputs()
    if change == 'kickoff': doc['games'][0]['kickoff_at'] = '2026-10-30T12:00:00+00:00'
    if change == 'official_id': doc['aliases'][0]['official_id'] = 'different-game'
    if change == 'quote': doc['quotes'][0]['odds']['home'] = 9.9
    if change == 'observation': doc['observations'][0]['evidence']['source_sha256'] = 'e' * 64
    if change == 'lineup': doc['lineups'][0]['players'][0]['player_id'] = 'different-player'
    with pytest.raises(structure.Invalid): structure.validate(doc)


def test_contract_identity_and_mapping_poisoning_fail_before_inference():
    doc, state, contract, verify = inputs()
    contract['team_mapping'] = {key: 'Away Name' if value == 'Home Name' else 'Home Name'
                                for key, value in contract['team_mapping'].items()}
    with pytest.raises(ValueError): call(doc, state, contract, verify)


def test_prefreeze_validation_retains_identity_checks_and_does_not_weaken_stored_schema():
    doc, _, _, _ = inputs()
    with pytest.raises(structure.Invalid): structure.validate_inference_core(doc)
    core = copy.deepcopy(doc)
    core.update(freezes=[], settlements=[], lineups=[])
    assert structure.validate_inference_core(core)
    with pytest.raises(structure.Invalid): structure.validate(core)
    core['products'][0]['canonical_game_id'] = 'wrong-game'
    with pytest.raises(structure.Invalid): structure.validate_inference_core(core)
