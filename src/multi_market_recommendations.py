"""Prospective automatic selections for exact full-game totals/handicaps.

The original winner policy is independent. M0 is KBO-only; explicit same-line
tipster votes can select other supported products. Neither is a profit claim.
"""
from copy import deepcopy
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import math
import re

from fixed_tipster_hybrid import stamp
from league_handicap import INTERNATIONAL_MODEL, MODELS as LEAGUE_HANDICAP_MODELS, compare_international
from league_handicap import recommend as league_handicap_pick
from market_pick_frontier import ARM_NAMES, FINAL, MODELS, choose_final, market_estimate, score_pick
from public_tipster_votes import kickoff

VERSION = 'multi-market-recommendations-v1'
RULE_SOURCE = 'https://www.sportstoto.co.kr/terms_allocation.php'


def official_options(options, sport, league):
    """Apply the named full-game Proto contract, rejecting explicit conflicts.

    These are published product rules, not evidence of an HTTP price receipt.
    Volleyball units need extra proof. KBO half-line handicaps apply the signed
    home adjustment even on an original draw, verified against official rows.
    """
    if sport not in {'sc', 'bs', 'bk'} or not league:
        raise ValueError('unsupported_or_unverified_sport_unit')
    period = 'regulation' if sport == 'sc' else 'full_including_extra'
    unit = {'sc': 'goals', 'bs': 'runs', 'bk': 'points'}[sport]
    market = options[0].get('market'); line = _number(options[0].get('line'))
    if line * 2 != int(line * 2):
        raise ValueError('quarter_line_not_supported')
    if market == '언더오버' and line >= 0 and line != int(line) and len(options) == 2:
        rule = 'total_half'
    elif market == '핸디캡' and len(options) == 3 and line == int(line):
        rule = 'handicap_3way'
    elif market == '핸디캡' and sport in {'bs', 'bk'} and len(options) == 2 and line != int(line):
        rule = 'handicap_half'
    else:
        raise ValueError('product_settlement_rule_unverified')
    out = []
    for option in options:
        if option.get('n_way') is not None and (type(option['n_way']) is not int or option['n_way'] != len(options)):
            raise ValueError('declared_selection_count_mismatch')
        pattern = r'H\s*([-+]?\d+(?:\.\d+)?)' if market == '핸디캡' else r'(?:U/O|[UO])?\s*([-+]?\d+(?:\.\d+)?)'
        match = re.fullmatch(pattern, str(option.get('label') or '').strip(), re.I)
        if match is None or _number(match[1]) != _number(option.get('line')):
            raise ValueError('label_line_mismatch')
        if option.get('period') not in (None, '', 'full', 'full_game', period):
            raise ValueError('conflicting_period')
        if option.get('result_period') not in (None, '', period):
            raise ValueError('conflicting_result_period')
        if option.get('unit') not in (None, '', unit) or option.get('settlement_rule') not in (None, '', rule):
            raise ValueError('conflicting_product_rule_or_unit')
        if option.get('settlement_scope') not in (None, '', 'full', 'full_game', period, rule, period+':'+rule):
            raise ValueError('conflicting_settlement_scope')
        out.append(dict(option, period=period, unit=unit, settlement_rule=rule))
    return out


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite number')
    return value


def build(game, rows, as_of, built_at, *, distribution=None, market_candidates=None, market_observed_at=None):
    """Pure except for the bounded local M0 provider; never HTTP or fitting."""
    result = {'version': VERSION, 'status': 'no_eligible_recommendations', 'items': [],
              'rejected': [], 'policy_authorized': True, 'validated_edge': False,
              'paper_wallet_changed': False, 'generated_at': built_at}
    try:
        start = kickoff(game); decision = stamp(as_of); finished = stamp(built_at)
        if finished < decision or max(decision, finished) >= start-timedelta(minutes=30):
            return dict(result, status='after_t30')
        if game.get('status') in {'cancelled', 'finished', 'ended', 'live', '정산', '결과확인', '취소', '경기중'} or game.get('no_odds') or game.get('betting_closed'):
            return dict(result, status='not_open_pregame')
        observed_price = game.get('live_market_observed_at') or market_observed_at
        price_time = stamp(observed_price or as_of)
        if price_time > decision:
            return dict(result, status='future_market_receipt')
    except (ValueError, TypeError, KeyError, AttributeError):
        return dict(result, status='invalid_prospective_clock')
    if distribution is None:
        try:
            from m0_forward_provider import get_distribution
            from research.m1_service.model import TEAM_NAMES
            from public_tipster_votes import team
            aliases = {team(name, 'KBO'): identity for identity, name in TEAM_NAMES.items()}
            request = {'league': game.get('league'), 'home_team_id': aliases.get(team(game.get('home'), 'KBO')),
                       'away_team_id': aliases.get(team(game.get('away'), 'KBO')),
                       'kickoff_at': start.isoformat(), 'season': start.year,
                       'date': start.date().isoformat()}
            distribution = get_distribution(request, as_of, built_at)
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            distribution = {'status': 'unavailable', 'reason': type(exc).__name__}
    result['m0_status'] = distribution.get('status', 'unavailable')
    from market_diversity import build_market_candidates
    candidates = market_candidates if market_candidates is not None else build_market_candidates(game, rows, as_of, built_at)
    groups = defaultdict(list)
    numbers = Counter()
    selections = Counter()
    for option in game.get('options', []):
        numbers[str(option.get('게임번호', ''))] += 1
        selections[option.get('selection_id')] += 1
        if option.get('market') in {'언더오버', '핸디캡'}:
            key = (str(option.get('게임번호', '')), option['market'], str(option.get('label') or ''),
                   str(option.get('line')))
            groups[key].append(option)
    if len(groups) > 32:
        return dict(result, status='market_group_limit_exceeded')
    from market_outcomes import normalize_market, probabilities
    for key, options in sorted(groups.items()):
        try:
            terms = normalize_market(official_options(options, game.get('sport'), game.get('league')),
                                     game.get('sport'), game.get('league'))
            offers = {o['선택']: o for o in options}
            if any(not o.get('selection_id') or not o.get('offer_id') or _number(o['배당']) <= 1 for o in options):
                raise ValueError('unidentified_or_invalid_offer')
            exact = (re.fullmatch(r'\d+', key[0]) and numbers[key[0]] == len(options)
                     and all(selections[o['selection_id']] == 1 for o in options)
                     and len({o['offer_id'] for o in options}) == len(options))
            model_arm = None
            if exact and observed_price:
                try:
                    model_arm = league_handicap_pick(terms, game, offers, price_time=price_time)
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    pass
            vote_matches = [c for c in candidates.get('offers', []) if c.get('status') == 'candidate'
                            and str(c.get('game_no')) == key[0] and c.get('market') == key[1]
                            and str(c.get('label') or '') == key[2]]
            vote = vote_matches[0] if len(vote_matches) == 1 else None
            if model_arm is None and any(c.get('status') == 'ambiguous_or_tied' and str(c.get('game_no')) == key[0]
                   and c.get('market') == key[1] and str(c.get('label') or '') == key[2]
                   for c in candidates.get('offers', [])):
                raise ValueError('tied_exact_line_tipster_votes')
            m0 = distribution.get('status') == 'available' and game.get('league') == 'KBO'
            operating_price = distribution.get('operating_price_evidence')
            if m0 and operating_price is not None:
                from kbo_operating_prices import matches_offer
                m0 = bool(exact and observed_price and matches_offer(operating_price, game, terms, offers, price_time))
            comparisons = {}
            score_masses = {}
            if m0:
                masses = probabilities(terms, distribution['home_mean'], distribution['away_mean'])
                score_masses['m0_actual'] = masses
                ranks = {s: p['p_win']/(1-p['p_push']) if p['p_push'] < 1 else 0 for s, p in masses.items()}
                leaders = [s for s, p in ranks.items() if abs(p-max(ranks.values())) < 1e-12]
                if len(leaders) == 1:
                    m0_side = leaders[0]
                    comparisons['m0_actual'] = {'selection': m0_side, 'probability': ranks[m0_side],
                        'selection_id': offers[m0_side]['selection_id'], 'offer_id': offers[m0_side]['offer_id'],
                        'odds': _number(offers[m0_side]['배당']), 'model': 'm0_actual',
                        'p_push': masses[m0_side]['p_push'], 'capture_hash': distribution['capture_hash'],
                        'estimated_roi': masses[m0_side]['p_win']*(_number(offers[m0_side]['배당'])-1)-masses[m0_side]['p_loss'],
                        'adopted': False, 'reason': 'retrospective_total_roi_not_superior'}
                else:
                    comparisons['m0_actual'] = {'status': 'withheld', 'reason': 'tied_m0_probabilities', 'adopted': False}
            if model_arm is not None:
                side = model_arm['selection']
                model = model_arm['model']
                probability = model_arm['probability']
                basis = model_arm['probability_basis']
            elif vote is not None and vote.get('selection') in offers:
                side = vote['selection']; model = 'tipster-market-vote-v1'
                probability = _number(offers[side]['시장확률']); basis = 'market_conditional_decisive'
                if not 0 < probability < 1:
                    raise ValueError('invalid_market_probability')
            else:
                if candidates.get('status') == 'tipster_input_unavailable' or any(
                        isinstance(r, dict) and r.get('_tipster_load_error') for r in rows or []):
                    raise ValueError('tipster_input_unavailable')
                minimum = min(_number(o['배당']) for o in options)
                leaders = [s for s, o in offers.items() if _number(o['배당']) == minimum]
                if len(leaders) != 1:
                    raise ValueError('tied_market_probabilities')
                side = leaders[0]; model = 'market-baseline-v1'
                total = sum(1/_number(o['배당']) for o in options)
                probability = (1/_number(offers[side]['배당']))/total
                basis = 'normalized_inverse_market_conditional_decisive'
            market_min = min(_number(o['배당']) for o in options)
            market_leaders = [s for s, o in offers.items() if _number(o['배당']) == market_min]
            if len(market_leaders) == 1:
                reference = offers[market_leaders[0]]
                comparisons['market'] = {'selection': market_leaders[0], 'model': 'market-baseline-v1',
                    'selection_id': reference['selection_id'], 'offer_id': reference['offer_id'], 'odds': _number(reference['배당'])}
            # The extra research comparison never replaces the operating side.
            # Refuse reissued/ambiguous numbers even if one group alone looks valid.
            if exact:
                if 'market' in comparisons:
                    comparisons['market'].update(market_estimate(market_leaders[0], offers,
                        model='market-baseline-v1', basis='market_inverse_odds'))
                if vote is not None and side == vote.get('selection'):
                    comparisons['frontier_tipster'] = market_estimate(side, offers,
                        model='frontier_tipster', basis='tipster_direction_market_odds')
                if model_arm is not None:
                    comparisons[model] = model_arm
                elif observed_price:
                    try:
                        research = compare_international(terms, game, offers, price_time=price_time)
                        if research is not None:
                            comparisons[INTERNATIONAL_MODEL] = research
                    except (OSError, ValueError, TypeError, KeyError, OverflowError):
                        pass  # Keep the regular pick if research inputs cannot be loaded.
                if m0:
                    companions = distribution.get('companions') or {}
                    for name in MODELS:
                        try:
                            model_masses = score_masses.get(name)
                            if model_masses is None:
                                rates = companions.get(name)
                                if not rates:
                                    continue
                                model_masses = probabilities(terms, rates['home_mean'], rates['away_mean'])
                            arm = score_pick('frontier_' + name, model_masses, offers)
                            if arm:
                                comparisons[arm['model']] = dict(arm, capture_hash=distribution['capture_hash'])
                        except (ValueError, TypeError, KeyError, OverflowError):
                            continue  # Invalid research data must not drop the operating recommendation.
            option = offers[side]; price = _number(option['배당'])
            evidence = (vote or {}).get('evidence') or []
            vote_summary = ({k: vote.get(k) for k in ('authors','vote_counts','selection','status')}
                            if vote is not None else None)
            if vote_summary is not None:
                vote_summary.update(evidence_hash=_hash(evidence), evidence_total=len(evidence),
                                    displayed_evidence_count=min(len(evidence),32))
            for current in options:
                current['period'] = 'full_game'
                current['result_period'] = terms['period']
                current['unit'] = terms['unit']
                current['settlement_rule'] = terms['settlement_rule']
                current['settlement_scope'] = terms['period']+':'+terms['settlement_rule']
            item = {'selection_id': option['selection_id'], 'offer_id': option['offer_id'],
                    'market': option['market'], 'label': option.get('label') or '', 'line': float(terms['line']),
                    'game_no': key[0], 'selection': side, 'odds': price, 'probability': probability,
                    'probability_basis': basis, 'model': model, 'terms': terms,
                    'settlement_rule': terms['settlement_rule'], 'period': 'full_game', 'result_period': terms['period'],
                    'p_push': model_arm['p_push'] if model_arm is not None else None,
                    'estimated_roi': model_arm['estimated_roi'] if model_arm is not None else None,
                    'validation_status': 'user_authorized_unvalidated', 'validated_edge': False,
                    'automatic_recommendation': True, 'candidate_only': False,
                    'source_ids': sorted({e['source_url'] for e in evidence})[:8] if vote else ['proto_official_current_market'],
                    'source_evidence': deepcopy(evidence[:32]), 'comparisons': comparisons,
                    'rule_source': RULE_SOURCE, 'tipster_vote': vote_summary, 'market_observed_at': price_time.isoformat(),
                    'selection_source': 'exact_line_vote' if vote else 'market_fallback_no_exact_line_votes'}
            if model_arm is not None:
                item.update(selection_source='frozen_league_handicap_model',
                            model_artifact_sha256=model_arm['model_artifact_sha256'],
                            source_ids=['proto_official_current_market', model])
            result['items'].append(item)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            result['rejected'].append({'game_no': key[0], 'market': key[1], 'label': key[2], 'reason': str(exc)[:140]})
    result['status'] = 'recommended' if result['items'] else result['status']
    if result['items']:
        choose_final(result['items'])
        result['pick_comparison_version'] = 'exact-market-frontier-v1'
    result['evidence_hash'] = _hash({k: v for k, v in result.items() if k != 'generated_at'})
    prior = game.get('market_recommendations') or {}
    if prior.get('version') == VERSION and prior.get('evidence_hash') == result['evidence_hash']:
        try:
            if stamp(prior['generated_at']) <= finished:
                result['generated_at'] = prior['generated_at']
        except (ValueError, TypeError, KeyError):
            pass
    return result


def result_for_offer(item, status):
    """Use the official exact-product grade; never infer it from winner picks."""
    if status not in {'hit', 'miss', 'void'}:
        raise ValueError('official_product_grade_required')
    price = Decimal(str(item['odds']))
    if not price.is_finite() or price <= 1:
        raise ValueError('invalid_frozen_price')
    net = price-1 if status == 'hit' else Decimal(-1) if status == 'miss' else Decimal(0)
    return {'result': status, 'selection_id': item['selection_id'], 'offer_id': item['offer_id'],
            'market': item['market'], 'line': item['line'], 'model': item['model'],
            'settlement_rule': item['settlement_rule'], 'odds': str(price), 'net_units': str(net),
            'paper_wallet_affected': False}


def performance(records):
    """Actual prospective per-product results; refunds stay in ROI denominator."""
    selected = []; unique = set(); comparison_context = defaultdict(list)
    for event, record in records.items():
        for item in (record.get('market_recommendations') or {}).get('items', []):
            unique.add(event)
            grade = (record.get('market_recommendation_results') or {}).get(item['offer_id'])
            selected.append((item, grade))
            terms = item.get('terms') or {}
            context = [terms.get('sport'), terms.get('league'), item.get('market'), item.get('settlement_rule')]
            line = item.get('line')
            if (type(line) in (int, float) and math.isfinite(line)
                    and all(isinstance(k, str) and k and '|' not in k for k in context)):
                line_key = format(Decimal(str(line or 0)).normalize(), 'f')
                for name, arm in (item.get('comparisons') or {}).items():
                    side = arm.get('selection')
                    if (name in ('market', 'frontier_tipster', FINAL, INTERNATIONAL_MODEL,
                                 *ARM_NAMES, *LEAGUE_HANDICAP_MODELS.values())
                            and isinstance(side, str) and side and '|' not in side
                            and arm.get('selection_id') and arm.get('offer_id')):
                        settled = ((grade or {}).get('comparisons') or {}).get(name)
                        comparison_context['|'.join([*context, line_key, side, name])].append((item, settled))
    def group(rows):
        final = [g for _, g in rows if g and g.get('result') in {'hit', 'miss', 'void'}]
        decided = [g for g in final if g['result'] != 'void']
        net = sum((Decimal(g['net_units']) for g in final), Decimal(0))
        hits = sum(g['result'] == 'hit' for g in decided)
        return {'captured': len(rows), 'bets': len(final), 'pending': len(rows)-len(final),
                'hits': hits, 'decided': len(decided), 'refunds': len(final)-len(decided),
                'hit_rate': hits/len(decided) if decided else None, 'net_units': str(net),
                'roi': float(net/len(final)) if final else None}
    comparison_rows = defaultdict(list)
    matched_rows = {'m0_actual': [], 'market': [], 'operating': []}
    for item, grade in selected:
        for name, arm in (item.get('comparisons') or {}).items():
            if arm.get('selection_id') and arm.get('offer_id'):
                comparison_rows[name].append((item, ((grade or {}).get('comparisons') or {}).get(name)))
        if all((item.get('comparisons') or {}).get(name, {}).get('selection_id') for name in ('m0_actual','market')):
            for name in ('m0_actual','market'):
                matched_rows[name].append((item, ((grade or {}).get('comparisons') or {}).get(name)))
            matched_rows['operating'].append((item, grade))
    return {**group(selected), 'by_market': {m: group([(i,g) for i,g in selected if i['market']==m])
                                            for m in sorted({i['market'] for i,_ in selected})},
            'by_model': {m: group([(i,g) for i,g in selected if i['model']==m])
                        for m in sorted({i['model'] for i,_ in selected})},
            'comparison_models': {name: group(rows) for name, rows in sorted(comparison_rows.items())},
            'comparison_by_context': {key: group(rows) for key, rows in sorted(comparison_context.items())},
            'matched_m0_comparison': {name: group(rows) for name, rows in matched_rows.items()},
            'unique_fixtures': len(unique), 'paper_wallet_changed': False,
            'scope': 'prospective_per_exact_product_not_independent_game_counts'}
