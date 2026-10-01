"""Independent audits of the incremental experiment's saved evidence."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_incremental_sources_selection_and_settlement():
    folder = ROOT / 'docs/research'
    data = json.loads((folder / '2026-10-01-dc-results.json').read_text(encoding='utf-8'))
    prior = json.loads((folder / '2026-10-01-score-tournament-results.json').read_text(encoding='utf-8'))
    assert data['sources'] == prior['sources']
    old_games = {g['match_id']: g for f in prior['folds'] for g in f['forecasts']}
    for fold in data['folds']:
        trials = [t for t in data['trials'] if t['inner_year'] == fold['inner_year']]
        rank = lambda t: (t['metrics']['logloss'], t['id'])
        assert len({t['id'] for t in trials}) == 30
        assert min(trials, key=rank)['id'] == fold['pooled_selected_id']
        for family, chosen in fold['selected_ids'].items():
            assert min((t for t in trials if t['config']['family'] == family), key=rank)['id'] == chosen
        games = {g['match_id']: g for g in fold['forecasts']}
        assert len(games) == 686
        for key, game in games.items():
            for field in ('league', 'date', 'y', 'hg', 'ag', 'prices'):
                assert game[field] == old_games[key][field]
        dates = {(g['league'], g['date']) for g in games.values()}
        for model, report in fold['reports'].items():
            for policy in report['policies'].values():
                ledger = policy['ledger']
                assert len(ledger) == len(dates)
                assert {(r['league'], r['date']) for r in ledger} == dates
                total, tickets, wins = Decimal(0), 0, 0
                for record in ledger:
                    legs = record['legs']
                    assert len(legs) in (0, 2)
                    assert record['stake'] == bool(legs)
                    assert len({l['match_id'] for l in legs}) == len(legs)
                    payout, won = Decimal(1), bool(legs)
                    for leg in legs:
                        game = games[leg['match_id']]
                        assert (game['league'], game['date']) == (record['league'], record['date'])
                        assert leg['odds'] == game['prices'][leg['outcome']]
                        assert leg['probability'] == game['probabilities'][model][leg['outcome']]
                        won &= game['y'] == leg['outcome']
                        payout *= Decimal(str(leg['odds']))
                    profit = (payout if won else Decimal(0)) - Decimal(record['stake'])
                    assert record['won'] == won
                    assert record['profit'] == pytest.approx(float(profit))
                    total += profit
                    tickets += bool(legs)
                    wins += won
                metrics = policy['metrics']
                assert metrics['tickets'] == tickets
                assert metrics['wins'] == wins
                assert metrics['profit'] == pytest.approx(float(total))
                assert metrics['roi'] == (pytest.approx(float(total / tickets)) if tickets else None)
