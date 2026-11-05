"""Frozen league models for exact full-game handicap products."""
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
INTERNATIONAL_ARTIFACT = ARTIFACT.with_name('international-handicap-v1.json')
INTERNATIONAL_SHA256 = '6bb8801c2f55c0adf6f39bf2bcc41ba1866a21901b4b583696a9ff18e83a6ff2'
INTERNATIONAL_MODEL = 'international-team-poisson-handicap-v1'
MODELS = {
    ('sc', 'K리그1', 'regulation', 'handicap_3way'): 'kleague1-team-poisson-handicap-v1',
    ('bk', 'KBL', 'full_including_extra', 'handicap_half'): 'kbl-team-margin-handicap-v1',
}
PROBABILITY_BASIS = 'frozen_league_handicap_score_model'
KST = timezone(timedelta(hours=9))


@lru_cache(maxsize=2)
def _models(path, expected_hash):
    raw = path.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('handicap model artifact hash mismatch')
    return json.loads(raw)


def poisson_rates(fitted, home, away):
    teams = fitted['teams']
    coefficients = fitted['coefficients']
    intercept, home_bonus = coefficients[:2]
    defense = 2 + len(teams)
    home_rate = exp(intercept + home_bonus + coefficients[2 + teams[home]] + coefficients[defense + teams[away]])
    away_rate = exp(intercept + coefficients[2 + teams[away]] + coefficients[defense + teams[home]])
    return home_rate, away_rate


def _teams_for_game(fitted, game, price_time):
    if price_time.astimezone(KST).date() <= date.fromisoformat(fitted['train_through']):
        return None
    aliases = fitted['aliases']
    home, away = (aliases.get(''.join(str(game.get(side, '')).split())) for side in ('home', 'away'))
    return (home, away) if home and away and home != away else None


def recommend(terms, game, offers, *, price_time):
    """Use the replayed selection rule and the exact current price vector."""
    model = MODELS.get((terms['sport'], terms['league'], terms['period'], terms['settlement_rule']))
    if terms['market'] != 'handicap' or model is None or price_time is None:
        return None
    fitted = _models(ARTIFACT, ARTIFACT_SHA256)[terms['league']]
    teams = _teams_for_game(fitted, game, price_time)
    if teams is None:
        return None
    home, away = teams
    if terms['sport'] == 'sc':
        arm = score_pick(model, probabilities(terms, *poisson_rates(fitted, home, away)), offers)
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


def compare_international(terms, game, offers, *, price_time):
    """Return a research pick while the caller retains the regular recommendation."""
    if (price_time is None or terms['sport'] != 'sc' or terms['market'] != 'handicap'
            or terms['period'] != 'regulation' or terms['settlement_rule'] != 'handicap_3way'):
        return None
    models = _models(INTERNATIONAL_ARTIFACT, INTERNATIONAL_SHA256)['models']
    fitted = next((value for value in models.values() if terms['league'] in value['league_names']), None)
    if fitted is None:
        return None
    teams = _teams_for_game(fitted, game, price_time)
    if teams is None:
        return None
    arm = score_pick(INTERNATIONAL_MODEL, probabilities(terms, *poisson_rates(fitted, *teams)), offers)
    if arm is not None:
        return dict(arm, model_artifact_sha256=INTERNATIONAL_SHA256)
    return None
