"""Normalize supported full-game market rows from the official G101 response."""
import math
import re

from market_outcomes import Market


TOTAL_IDS = {'sc': ('78', '7', '축구 언더오버'), 'bs': ('79', '7', '야구 언더오버'),
             'bk': ('80', '7', '농구 언더오버')}


def full_game_product(row, sport):
    """Return only established whole-game product codes and matching price shapes."""
    if row.get('gameSubject') not in (None, ''):
        return None
    code = tuple(row.get(field) for field in ('betId', 'betTypId', 'betNm'))
    legacy = code == (None, None, None)
    period = 'regulation' if sport == 'sc' else 'full_including_extra'
    number = str(row['matchSeq'])
    if not re.fullmatch(r'\d+', number):
        return None
    if row['handi'] == 9 and (legacy or code == TOTAL_IDS[sport]):
        if row.get('drawAllot') or row.get('winHandi') != row.get('loseHandi'):
            return None
        market = Market(number, 'total', str(row['winHandi']), period, 'total_half')
        price_fields = ('winAllot', 'loseAllot')
    elif row['handi'] == 2 and (legacy or (sport == 'sc' and code == ('5', '4', '축구 핸디캡'))):
        line = row['winHandi']
        if row.get('loseHandi') != -line or (sport != 'sc' and row.get('drawAllot')):
            return None
        rule = 'handicap_3way' if sport == 'sc' else 'handicap_half'
        market = Market(number, 'handicap', str(line), period, rule)
        price_fields = ('winAllot', 'drawAllot', 'loseAllot') if sport == 'sc' else ('winAllot', 'loseAllot')
    else:
        return None
    prices = {side: float(row[field]) for side, field in zip(market.selections, price_fields)}
    return (market, prices) if all(math.isfinite(v) and v > 1 for v in prices.values()) else None
