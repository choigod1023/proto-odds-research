"""Frozen K League 1 and KBL full-game handicap recommendations."""
from datetime import date, timedelta, timezone
from functools import lru_cache
import hashlib
import json
from math import erf, exp, sqrt
from pathlib import Path

from market_outcomes import probabilities
from market_pick_frontier import estimate, score_pick

ARTIFACT = Path(__file__).resolve().parents[1] / 'data/model_candidates/handicap-v1.json'
ARTIFACT_SHA256 = 'afd2f2eeb6afce482a95c0494e860ab849115e98feccb5c15175242241484bfd'
MODELS = {
    ('sc', 'K리그1', 'regulation', 'handicap_3way'): 'kleague1-team-poisson-handicap-v1',
    ('bk', 'KBL', 'full_including_extra', 'handicap_half'): 'kbl-team-margin-handicap-v1',
}
PROBABILITY_BASIS = 'frozen_league_handicap_score_model'
KST = timezone(timedelta(hours=9))


@lru_cache(maxsize=1)
def _models():
    raw = ARTIFACT.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != ARTIFACT_SHA256:
        raise ValueError('handicap model artifact hash mismatch')
    return json.loads(raw)


def recommend(terms, game, offers, *, price_time):
    """Use the replayed selection rule and the exact current price vector."""
    model = MODELS.get((terms['sport'], terms['league'], terms['period'], terms['settlement_rule']))
    if terms['market'] != 'handicap' or model is None or price_time is None:
        return None
    fitted = _models()[terms['league']]
    if price_time.astimezone(KST).date() <= date.fromisoformat(fitted['train_through']):
        return None
    names = fitted['aliases']
    home = names.get(''.join(str(game.get('home', '')).split()))
    away = names.get(''.join(str(game.get('away', '')).split()))
    if not home or not away or home == away:
        return None
    if terms['sport'] == 'sc':
        teams = fitted['teams']
        coefficients = fitted['coefficients']
        intercept, home_bonus = coefficients[:2]
        defense = 2 + len(teams)
        home_rate = exp(intercept + home_bonus + coefficients[2 + teams[home]] + coefficients[defense + teams[away]])
        away_rate = exp(intercept + coefficients[2 + teams[away]] + coefficients[defense + teams[home]])
        arm = score_pick(model, probabilities(terms, home_rate, away_rate), offers)
        if arm is None:
            return None
    else:
        residual = fitted['team_residual']
        margin = fitted['mean_margin'] + (residual[home] - residual[away]) / 2
        z = (-float(terms['line']) - margin) / (fitted['sd_margin'] * sqrt(2))
        p_home = 1 - .5 * (1 + erf(z))
        side, win = ('핸디홈', p_home) if p_home >= .5 else ('핸디원정', 1 - p_home)
        arm = estimate(side, offers[side], win, 0., 1 - win, basis=PROBABILITY_BASIS, model=model)
    return dict(arm, probability_basis=PROBABILITY_BASIS, model_artifact_sha256=ARTIFACT_SHA256)
