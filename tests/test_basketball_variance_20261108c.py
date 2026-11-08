import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'experiments/basketball-variance-20261108c/run.py'
spec = importlib.util.spec_from_file_location('basketball_variance', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_student_and_normal_same_winner_pick_and_probability_is_valid():
    model = {'mean': 1., 'sd': 12., 'teams': {'a': 1., 'b': -1., 'c': 6.}}
    for a, b in [('a', 'b'), ('b', 'c'), ('c', 'a')]:
        normal, student = module.margin_prob(model, a, b)
        assert 0 < normal < 1 and 0 < student < 1
        assert (normal >= .5) == (student >= .5)
    assert module.margin_prob(model, 'missing', 'a') is None


def test_join_rejects_duplicate_quotes_and_conflicting_results():
    game = {'day': '2026-01-01', 'home': '1', 'away': '2', 'margin': 3}
    quote = {'day': game['day'], 'sport': 'bk', 'league': 'KBL', 'home': 'A', 'away': 'B',
             'y': 0, 'odds': [1.5, 2.4]}
    aliases = {'A': '1', 'B': '2'}
    joined, counts = module.join([game], [quote, quote], aliases)
    assert joined == [] and counts['missing_or_ambiguous_fixture'] == 1
    joined, counts = module.join([game], [dict(quote, y=1)], aliases)
    assert joined == [] and counts['winner_label_conflict'] == 1


def test_metrics_price_and_daily_drawdown():
    rows = [{'day': '2026-01-01', 'y': 1, 'odds': [1.5, 2.4], 'prob': {'x': .6}},
            {'day': '2026-01-02', 'y': 0, 'odds': [1.5, 2.4], 'prob': {'x': .6}}]
    result = module.metrics(rows, 'x')
    assert result['accuracy'] == .5
    assert result['conditional_roi'] == pytest.approx(-.25)
    assert result['daily_mdd_units'] == pytest.approx(1.)


def test_training_is_invariant_to_future_outcomes():
    games = [{'day': '2025-01-01', 'home': 'a', 'away': 'b', 'margin': 10},
             {'day': '2025-01-03', 'home': 'b', 'away': 'a', 'margin': -4},
             {'day': '2026-01-01', 'home': 'a', 'away': 'b', 'margin': 20}]
    train = lambda rows: [g for g in rows if '2025-01-01' <= g['day'] <= '2025-12-27']
    original = module.fit(train(games))
    games[-1]['margin'] = -100
    assert module.fit(train(games)) == original
