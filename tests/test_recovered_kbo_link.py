from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.verify_recovered_kbo_link import verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs():
    files = ['experiments/input-link-recovery-20261102/production-snapshot.json',
        'experiments/verified-fact-capture-20261031/capture-with-bytes.json',
        'experiments/input-link-recovery-20261102/kbo-result-20261101.json']
    return [json.loads((ROOT / p).read_text(encoding='utf8')) for p in files]


def test_real_recovered_record_links_without_claiming_original_price_or_lineup(inputs):
    original = deepcopy(inputs)
    report = verify(*inputs)
    assert report['recovered_operating_links'] == 1
    assert report['ledger_tail']['rows'] == 256 and not report['ledger_tail']['full_prefix_verified']
    assert report['official_game_id'] == '20261101HTSS0'
    assert report['result_score'] == [6, 1] and report['grade'] == 'hit'
    assert report['descriptive_single_unit_profit'] == '0.73'
    assert report['price_link_kind'] == 'frozen_vector_with_predecision_corroboration'
    assert report['exact_external_original_price_snapshot'] is False
    assert report['pitcher_status'] == 'probable_not_final_confirmed'
    assert not report['confirmed_player_model_eligible'] and not report['model_uplift_evaluated']
    assert inputs == original


def test_record_tampering_is_rejected(inputs):
    inputs[0]['prediction_tail'][0]['record']['predictions'] = {'odds': 99}
    with pytest.raises(ValueError, match='record hash'):
        verify(*inputs)


def test_chain_gap_is_rejected(inputs):
    del inputs[0]['prediction_tail'][20]
    with pytest.raises(ValueError, match='broken tail chain'):
        verify(*inputs)


def test_late_fact_cannot_become_pregame_evidence(inputs):
    inputs[1]['requests'][0]['received_at'] = '2026-11-02T00:00:00+00:00'
    with pytest.raises(ValueError):
        verify(*inputs)


def test_future_prices_are_not_used_to_corroborate_frozen_price(inputs):
    for price in inputs[0]['fixture_prices']:
        price['observed_at'] = '2026-11-01T04:00:00+00:00'
    with pytest.raises(ValueError, match='predecision price'):
        verify(*inputs)


def test_other_side_price_change_is_not_hidden(inputs):
    for price in inputs[0]['fixture_prices']:
        if price['market_family'] == '승패':
            price['odds'][1] = 99
    with pytest.raises(ValueError, match='frozen price differs'):
        verify(*inputs)


def test_source_bytes_hash_is_verified(inputs):
    inputs[2]['request']['raw_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source bytes mismatch'):
        verify(*inputs)
