"""Pure market math. Terms must come from the exact official product.

No rule here proves a product exists or was available. Caller must bind it to
an official market and an appropriate period before using it operationally.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from scipy.stats import poisson, skellam


@dataclass(frozen=True)
class Market:
    game_no: str
    kind: str  # total, handicap, winner
    line: str | None
    period: str  # regulation or full_including_extra
    rule: str  # total_push, handicap_3way, winner_2way_draw_refund, winner_3way

    def __post_init__(self):
        if not isinstance(self.game_no, str) or not self.game_no.strip():
            raise ValueError('game number required')
        if self.period not in ('regulation', 'full_including_extra'):
            raise ValueError('unsupported period')
        allowed = {'total': ('total_push', 'total_half'), 'handicap': ('handicap_3way',),
                   'winner': ('winner_2way_draw_refund', 'winner_3way')}
        if self.rule not in allowed.get(self.kind, ()):
            raise ValueError('unsupported market rule; hold')
        if self.kind == 'winner':
            if self.line is not None:
                raise ValueError('winner must have no line')
        else:
            try:
                if isinstance(self.line, bool) or self.line is None:
                    raise ValueError('line required')
                value = Decimal(str(self.line))
                if not value.is_finite() or value * 2 != (value * 2).to_integral_value():
                    raise ValueError('only integer and half lines supported')
                if self.kind == 'total' and value < 0:
                    raise ValueError('negative total')
                if self.rule == 'total_half' and value == value.to_integral_value():
                    raise ValueError('integer total settlement not established')
                if self.rule == 'handicap_3way' and value != value.to_integral_value():
                    raise ValueError('three-way handicap requires integer line')
                object.__setattr__(self, 'line', str(value.normalize()))
            except InvalidOperation as exc:
                raise ValueError('invalid line') from exc

    @property
    def selections(self):
        if self.kind == 'total':
            return ('under', 'over')
        if self.rule in ('handicap_3way', 'winner_3way'):
            return ('home', 'draw', 'away')
        return ('home', 'away')


def same_market(a, b):
    return isinstance(a, Market) and a == b


def _probabilities(market, home_rate, away_rate):
    """Return per-selection win, loss, push; handicap is home score + line."""
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in (home_rate, away_rate)):
        raise ValueError('positive finite rates required')
    if market.kind == 'total':
        line = Decimal(market.line); f = float(line); mean = home_rate + away_rate
        push = float(poisson.pmf(int(line), mean)) if line == line.to_integral_value() else 0.0
        under = float(poisson.cdf(math.ceil(f)-1, mean))
        wins = {'under': under, 'over': float(poisson.sf(math.floor(f), mean))}
    else:
        line = Decimal(market.line) if market.kind == 'handicap' else Decimal(0)
        threshold = -line; f = float(threshold)
        equal = float(skellam.pmf(int(threshold), home_rate, away_rate)) if threshold == threshold.to_integral_value() else 0.0
        home = float(skellam.sf(math.floor(f), home_rate, away_rate))
        away = float(skellam.cdf(math.ceil(f)-1, home_rate, away_rate))
        push = equal if market.rule == 'winner_2way_draw_refund' else 0.0
        wins = {'home': home, 'away': away}
        if 'draw' in market.selections:
            wins['draw'] = equal
    return {side: {'win': wins[side], 'push': push, 'loss': max(0.0, 1.0-wins[side]-push)} for side in market.selections}


def _settle(market, selection, home_score=None, away_score=None, *, result_status, result_period):
    """Return win/loss/refund/hold; cancellation must be official, never inferred."""
    if selection not in market.selections:
        raise ValueError('invalid selection')
    if result_period != market.period:
        return 'hold'
    if result_status == 'official_void':
        return 'refund'
    if result_status != 'official_final':
        return 'hold'
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (home_score, away_score)):
        return 'hold'
    if market.kind == 'total':
        value = Decimal(home_score + away_score) - Decimal(market.line)
        if value == 0:
            return 'refund'
        winner = 'over' if value > 0 else 'under'
    else:
        value = Decimal(home_score-away_score) + (Decimal(market.line) if market.kind == 'handicap' else 0)
        if value == 0 and market.rule == 'winner_2way_draw_refund':
            return 'refund'
        winner = 'home' if value > 0 else 'away' if value < 0 else 'draw'
    return 'win' if selection == winner else 'loss'


ALIASES = {'핸디홈': 'home', '핸디무': 'draw', '핸디원정': 'away',
           '언더': 'under', '오버': 'over', '홈': 'home', '무': 'draw',
           '원정': 'away', '승': 'home', '패': 'away'}
KINDS = {'핸디캡': 'handicap', '언더오버': 'total', '승패': 'winner', '승무패': 'winner'}


def _side(value):
    return ALIASES.get(value, value)


def normalize_market(options, sport, league):
    """Normalize one complete explicit product. Missing/ambiguous terms raise ValueError.

    Each option supplies market, 게임번호/game_no, line, period, settlement_rule,
    unit, and 선택/selection. Handicap line is signed HOME score adjustment.
    No period or settlement is inferred from league or a price label.
    """
    if not isinstance(options, list) or not options or not sport or not league:
        raise ValueError('options, sport and league required')
    identities, sides = [], []
    for o in options:
        if not isinstance(o, dict):
            raise ValueError('option must be a mapping')
        kind = KINDS.get(o.get('market'), o.get('market'))
        number = o.get('game_no', o.get('게임번호'))
        if number is None or isinstance(number, bool):
            raise ValueError('game number required')
        m = Market(str(number), kind, o.get('line'), o.get('period'), o.get('settlement_rule'))
        unit = o.get('unit')
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError('score unit required')
        identities.append((m, unit))
        sides.append(_side(o.get('selection', o.get('선택'))))
    market, unit = identities[0]
    if any(item != identities[0] for item in identities) or len(sides) != len(set(sides)) or set(sides) != set(market.selections):
        raise ValueError('incomplete, duplicate or mismatched market')
    return {'market': market.kind, 'game_no': market.game_no, 'line': market.line,
            'n_way': len(sides), 'period': market.period, 'settlement_rule': market.rule,
            'unit': unit, 'sport': sport, 'league': league}


def _from_terms(terms):
    if not isinstance(terms, dict) or not terms.get('sport') or not terms.get('league') or not terms.get('unit'):
        raise ValueError('complete normalized terms required')
    m = Market(terms.get('game_no'), terms.get('market'), terms.get('line'), terms.get('period'), terms.get('settlement_rule'))
    if terms.get('n_way') != len(m.selections):
        raise ValueError('incorrect number of selections')
    return m


def probabilities(terms, home_mean, away_mean):
    """Selection win probability is unconditional; push remains separate."""
    m = _from_terms(terms)
    labels = ({'under': '언더', 'over': '오버'} if m.kind == 'total' else
              {'home': '핸디홈', 'draw': '핸디무', 'away': '핸디원정'} if m.kind == 'handicap' else
              {'home': '홈', 'draw': '무', 'away': '원정'})
    return {labels[side]: {'p_win': v['win'], 'p_push': v['push'], 'p_loss': v['loss'],
                           'probability': v['win']} for side, v in _probabilities(m, home_mean, away_mean).items()}


def settle(terms, selection, home_score=None, away_score=None, status='final', *, result_period=None):
    """Final/void must be official; result_period explicitly proves score scope."""
    m = _from_terms(terms)
    state = _settle(m, _side(selection), home_score, away_score,
                    result_status={'final': 'official_final', 'void': 'official_void'}.get(status, status),
                    result_period=result_period)
    return {'state': state, 'hit': True if state == 'win' else False if state == 'loss' else None,
            'refund': state == 'refund', 'net_odds_factor': 1.0 if state == 'refund' else 0.0 if state == 'loss' else None}
