"""Recompute committed research settlements without network or Git history."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def audit_record(record, matches):
    legs = record['legs']
    assert record['budget'] == 1
    assert len(legs) in (0, 2)
    assert len({leg['match_id'] for leg in legs}) == len(legs)
    for leg in legs:
        match = matches[leg['match_id']]
        assert (match['league'], match['date']) == (record['league'], record['date'])
        assert leg['odds'] == match['prices'][leg['outcome']]
    won = int(bool(legs) and all(matches[leg['match_id']]['y'] == leg['outcome'] for leg in legs))
    stake = int(bool(legs))
    profit = won * float(np.prod([leg['odds'] for leg in legs])) - stake
    assert record['won'] == won
    assert record['stake'] == stake
    assert record['profit'] == pytest.approx(profit, abs=1e-10)


def test_all_committed_lagged_ledgers_and_aggregates():
    result = json.loads((ROOT/'docs/research/2026-10-01-lagged-information-results.json').read_text(encoding='utf-8'))
    audited = 0
    for fold in result['folds']:
        matches = {r['match_id']: r for r in fold['forecasts']}
        expected_days = None
        for report in fold['reports'].values():
            for policy in report['policies'].values():
                ledger, metrics = policy['ledger'], policy['metrics']
                days = [(r['league'], r['date']) for r in ledger]
                assert len(set(days)) == len(days)
                if expected_days is None:
                    expected_days = days
                assert days == expected_days
                for record in ledger:
                    audit_record(record, matches)
                    audited += 1
                stake = sum(r['stake'] for r in ledger)
                wins = sum(r['won'] for r in ledger)
                profit = sum(r['profit'] for r in ledger)
                assert metrics['days'] == len(days)
                assert metrics['tickets'] == stake
                assert metrics['wins'] == wins
                assert metrics['profit'] == pytest.approx(profit)
                assert metrics['coverage'] == pytest.approx(stake / len(days))
                assert metrics['budget_return'] == pytest.approx(profit / len(days))
                if stake:
                    assert metrics['roi'] == pytest.approx(profit / stake)
                    assert metrics['hit'] == pytest.approx(wins / stake)
                else:
                    assert metrics['roi'] is None and metrics['hit'] is None
    assert audited == 24100


def test_audit_detects_mutated_settlement():
    matches = {str(i): dict(league='D1', date='2024-01-01', prices=[2, 3, 4], y=0) for i in range(2)}
    record = dict(league='D1', date='2024-01-01', budget=1, stake=1, won=1, profit=3,
                  legs=[dict(match_id=str(i), outcome=0, odds=2) for i in range(2)])
    audit_record(record, matches)
    for field, bad in [('profit', 103), ('won', 0), ('stake', 0), ('budget', 2)]:
        mutated = copy.deepcopy(record)
        mutated[field] = bad
        with pytest.raises(AssertionError):
            audit_record(mutated, matches)
