from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_operating_roi import evaluate, compare
from prediction_ledger import LedgerCorruptionError
from test_live_ledger_settlement import setup_runtime, settle_live_market_results


def test_real_ledger_chain_and_saved_price(tmp_path):
    runtime, _, odds = setup_runtime(tmp_path)
    saved = runtime.records()[0]
    assert saved['features']['fixture']['round'] == 102
    assert saved['features']['fixture']['home'] == 'KIA'
    before = evaluate(runtime.records())
    assert before['descriptive_all_selected_offers']['n'] == 0
    settle_live_market_results(odds, runtime)
    result = evaluate(runtime.records())
    assert result['hash_chain_verified'] is True
    assert result['descriptive_all_selected_offers']['n'] == 1
    assert result['descriptive_all_selected_offers']['unit_stake_roi'] == pytest.approx(.55)
    assert result['monthly_loss_limit'] == 50000
    assert result['promotion_allowed'] is False
    corrupted = deepcopy(runtime.records())
    corrupted[0]['predictions']['odds'] = 99
    with pytest.raises(LedgerCorruptionError):
        evaluate(corrupted)


def test_late_training_labels_are_excluded():
    rows = [dict(captured='2026-10-01T00:00:00Z', known='2026-10-11T00:00:00Z',
                 probability=.6, odds=1.6, market='m', hit=1)] * 150
    result = compare(rows)
    assert result['status'] == 'insufficient_samples'
    assert result['train'] == 0
