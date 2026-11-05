"""Research pick selection on exact, already-validated pregame products."""
import math

MODELS = ('m0_actual', 'm0_projected', 'm1_projected')
ARM_NAMES = tuple('frontier_' + name for name in MODELS)
FINAL = 'frontier_consensus'
MIN_PROBABILITY = 0.55
EPSILON = 1e-10


def estimate(side, offer, p_win, p_push, p_loss, *, basis, model):
    odds = float(offer['배당'])
    if (not math.isfinite(odds) or odds <= 1
            or any(not math.isfinite(p) or not 0 <= p <= 1 for p in (p_win, p_push, p_loss))
            or abs(p_win + p_push + p_loss - 1) > EPSILON or p_push == 1):
        raise ValueError('invalid pick probability or price')
    return {'selection': side, 'selection_id': offer['selection_id'], 'offer_id': offer['offer_id'],
            'odds': odds, 'model': model, 'probability_basis': basis,
            'probability': p_win/(1-p_push), 'p_push': p_push,
            'estimated_roi': p_win*(odds-1)-p_loss}


def qualifies(row):
    return row['probability'] >= MIN_PROBABILITY and row['estimated_roi'] > EPSILON


def score_pick(model, masses, offers):
    rows = [estimate(side, offers[side], p['p_win'], p['p_push'], p['p_loss'],
                     basis='score_model', model=model) for side, p in masses.items()]
    qualified = [row for row in rows if qualifies(row)]
    # If nothing passes both conditions, show the most likely side for
    # comparison, never treat it as eligible for the final pick.
    choices = qualified or rows
    metric = 'estimated_roi' if qualified else 'probability'
    best = max(row[metric] for row in choices)
    leaders = [row for row in choices if abs(row[metric]-best) <= EPSILON]
    return leaders[0] if len(leaders) == 1 else None


def market_estimate(side, offers, *, model, basis):
    inv = {name: 1/float(offer['배당']) for name, offer in offers.items()}
    p = inv[side]/sum(inv.values())
    # Supported half lines and 3-way handicaps have no score-based push.
    # Event cancellation is unknown; this reference is conditional on play.
    return estimate(side, offers[side], p, 0., 1-p, basis=basis, model=model)


def choose_final(items):
    """At most one research pick, using the weakest of the three estimates."""
    eligible = []
    for item in items:
        arms = [item['comparisons'].get(name) for name in ARM_NAMES]
        if not all(row and qualifies(row) for row in arms):
            continue
        if len({(row['selection_id'], row['offer_id'], row['odds']) for row in arms}) != 1:
            continue
        candidate = dict(arms[0], model=FINAL, probability_basis='score_model_min',
                         probability=min(row['probability'] for row in arms),
                         estimated_roi=min(row['estimated_roi'] for row in arms))
        eligible.append((item, candidate))
    if not eligible:
        return
    best_roi = max(row['estimated_roi'] for _, row in eligible)
    leaders = [(item, row) for item, row in eligible if best_roi-row['estimated_roi'] <= EPSILON]
    best_probability = max(row['probability'] for _, row in leaders)
    leaders = [(item, row) for item, row in leaders if best_probability-row['probability'] <= EPSILON]
    if len(leaders) == 1:
        item, row = leaders[0]
        item['comparisons'][FINAL] = row
