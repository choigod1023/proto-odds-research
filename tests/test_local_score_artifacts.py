"""Independent checks of published score-model evidence, without rerunning models."""
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / 'docs/research/2026-10-01-score-tournament-results.json'


def evidence_path(name):
    """Recorded paths may originate from Windows; interpret them portably."""
    return ROOT.joinpath(*name.replace('\\', '/').split('/'))


@pytest.mark.parametrize('name', ['scripts/local_score_model_tournament.py',
                                  r'scripts\local_score_model_tournament.py'])
def test_evidence_path_platform_independent(name):
    assert evidence_path(name) == ROOT / 'scripts' / 'local_score_model_tournament.py'
    assert evidence_path(name).is_file()


def test_score_artifact_provenance_and_inner_selection():
    data = json.loads(RESULT.read_text(encoding='utf-8'))
    assert data['trial_count'] == len(data['trials']) == 60
    assert data['actual_league_fits'] == 32
    assert sum(s['matches'] for s in data['sources']) == 4116
    protocol = ROOT / 'docs/research/2026-10-01-score-tournament-protocol.md'
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == data['protocol_sha256']
    for name, digest in data['code_sha256'].items():
        # Preserve recorded execution-byte hashes across Git LF/CRLF checkout.
        lf = evidence_path(name).read_bytes().replace(b'\r\n', b'\n')
        assert digest in {hashlib.sha256(b).hexdigest() for b in (lf, lf.replace(b'\n', b'\r\n'))}
    for fold in data['folds']:
        trials = [t for t in data['trials'] if t['inner_year'] == fold['inner_year']]
        assert len({t['id'] for t in trials}) == 30
        key = lambda t: (t['metrics']['logloss'], t['id'])
        assert fold['pooled_selected_id'] == min(trials, key=key)['id']
        for family, chosen in fold['selected_ids'].items():
            assert chosen == min((t for t in trials if t['family'] == family), key=key)['id']


def test_score_artifact_forecasts_and_decimal_settlement():
    data = json.loads(RESULT.read_text(encoding='utf-8'))
    for fold in data['folds']:
        games = {r['match_id']: r for r in fold['forecasts']}
        assert len(games) == fold['outer_matches'] == 686
        days = {(r['league'], r['date']) for r in games.values()}
        for name, report in fold['reports'].items():
            loss = 0.
            for game in games.values():
                probs = game['probabilities'][name]
                assert sum(probs) == pytest.approx(1.)
                assert all(0 < p < 1 for p in probs)
                loss -= math.log(probs[game['y']])
            assert report['metrics']['logloss'] == pytest.approx(loss / len(games))
            for policy in report['policies'].values():
                ledger, metrics = policy['ledger'], policy['metrics']
                assert len(ledger) == len(days)
                assert {(r['league'], r['date']) for r in ledger} == days
                profit, tickets, wins = Decimal(0), 0, 0
                for record in ledger:
                    legs = record['legs']
                    assert record['stake'] == bool(legs)
                    assert len(legs) in (0, 2)
                    assert len({l['match_id'] for l in legs}) == len(legs)
                    payout, won = Decimal(1), bool(legs)
                    for leg in legs:
                        game = games[leg['match_id']]
                        assert (game['league'], game['date']) == (record['league'], record['date'])
                        assert leg['odds'] == game['prices'][leg['outcome']]
                        assert leg['probability'] == game['probabilities'][name][leg['outcome']]
                        won &= game['y'] == leg['outcome']
                        payout *= Decimal(str(leg['odds']))
                    net = (payout if won else Decimal(0)) - Decimal(record['stake'])
                    assert record['won'] == won
                    assert record['profit'] == pytest.approx(float(net))
                    profit += net
                    tickets += record['stake']
                    wins += won
                assert metrics['profit'] == pytest.approx(float(profit))
                assert metrics['tickets'] == tickets
                assert metrics['wins'] == wins
                assert metrics['roi'] == (pytest.approx(float(profit / tickets)) if tickets else None)
