"""Correctly scoped estimated payoffs and a non-promoting low-odds review.

    Win: odds-1, loss: -1, push: 0. A high hit rate alone does not imply profit.
    M1-derived markets remain uncalibrated; these are research candidates only.
"""
from decimal import Decimal
import math
from scipy.stats import poisson, skellam


def _number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError('finite numeric value required')
    return float(x)


def estimate(forecast, offer):
    if offer['fixture_id'] != forecast['fixture_id'] or offer['period'] != 'full_game':
        raise ValueError('same fixture and full-game period required')
    odds = _number(offer['odds'])
    if odds <= 1:
        raise ValueError('decimal odds must exceed one')
    kind, side, rule = offer['kind'], offer['selection'], offer['settlement_rule']
    h, a = map(_number, (forecast['home_rate'], forecast['away_rate']))
    if min(h, a) <= 0:
        raise ValueError('positive score rates required')
    if kind == 'full_game_2way':
        if side not in ('home', 'away') or rule != 'draw_void_refund':
            raise ValueError('wrong two-way terms')
        win = forecast['probabilities_3way'][side]
        push = forecast['probabilities_3way']['draw']
    elif kind == 'full_game_3way':
        if side not in ('home', 'draw', 'away') or rule != 'draw_is_outcome':
            raise ValueError('wrong three-way terms')
        win, push = forecast['probabilities_3way'][side], 0.
    elif kind in ('full_game_total', 'full_game_handicap'):
        line = _number(offer['line'])
        twice = Decimal(str(line))*2
        if twice != twice.to_integral_value() or rule != 'push_refund':
            raise ValueError('only explicit whole/half lines and push refund supported')
        whole = line.is_integer()
        if kind == 'full_game_total':
            if line < 0 or side not in ('under', 'over'):
                raise ValueError('invalid full-game total')
            push = float(poisson.pmf(int(line), h+a)) if whole else 0.
            win = float(poisson.cdf(math.ceil(line)-1, h+a)) if side == 'under' else float(poisson.sf(math.floor(line), h+a))
        else:
            if side not in ('home', 'away') or offer.get('line_basis') != 'home_score_plus_line':
                raise ValueError('explicit home handicap orientation required')
            threshold = -line
            push = float(skellam.pmf(int(threshold), h, a)) if whole else 0.
            homewin = float(skellam.sf(math.floor(threshold), h, a))
            win = homewin if side == 'home' else 1-homewin-push
    else:
        raise ValueError('unsupported market; never reuse moneyline probability')
    loss = 1-win-push
    if any(not math.isfinite(x) or x < -1e-10 or x > 1+1e-10 for x in (win, push, loss)):
        raise ValueError('invalid outcome mass')
    win, push, loss = [min(1., max(0., x)) for x in (win, push, loss)]
    return {'offer_id': offer['offer_id'], 'kind': kind, 'selection': side, 'odds': odds,
            'p_win': win, 'p_push': push, 'p_loss': loss,
            'conditional_decisive_hit_probability': win/(1-push) if push < 1 else None,
            'estimated_net_roi': win*(odds-1)-loss,
            'line': offer.get('line'), 'calibrated': False,
            'affects_selection': False, 'scope': 'frozen_unvalidated_poisson_research'}


def review(forecast, offers, original_offer_id):
    """Return a Pareto comparison, never an unvalidated automatic replacement."""
    ids = [o['offer_id'] for o in offers]
    if len(ids) != len(set(ids)) or ids.count(original_offer_id) != 1:
        raise ValueError('unique offers and exact original selection required')
    original = next(o for o in offers if o['offer_id'] == original_offer_id)
    if _number(original['odds']) <= 1:
        raise ValueError('original decimal odds must exceed one')
    trigger = Decimal(str(original['odds'])) <= Decimal('1.20')
    candidates, excluded = [], []
    for offer in offers:
        try:
            candidates.append(estimate(forecast, offer))
        except (ValueError, KeyError) as exc:
            excluded.append({'offer_id': offer['offer_id'], 'reason': str(exc)})
    positive = [c for c in candidates if c['estimated_net_roi'] > 1e-12]
    frontier = [c for c in positive if not any(
        d['p_win'] >= c['p_win'] and d['estimated_net_roi'] >= c['estimated_net_roi']
        and (d['p_win'] > c['p_win'] or d['estimated_net_roi'] > c['estimated_net_roi'])
        for d in positive)]
    return {'trigger_at_or_below_1_20': trigger, 'original_offer_id': original_offer_id,
            'candidates': candidates, 'excluded': excluded,
            'positive_estimated_ev_frontier_offer_ids': sorted(c['offer_id'] for c in frontier),
            'suggested_action': 'abstain_unvalidated_alternative' if trigger else 'research_only',
            'automatic_replacement': None, 'validated_edge': False,
            'reason': 'Positive estimated EV is not a calibrated profitable edge; M1 is not promoted.',
            'production_selection_changed': False}
