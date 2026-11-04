"""Plan historical settlement from official scores and immutable frozen terms.

This is separate from exact-offer settlement. No data is fetched and no price,
selection, prediction, or prior settlement is modified.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

if __package__:
    from src.official_result_backfill import (_official_source, _aware_timestamp, _game_kickoff,
                                              _latest_predictions, event_id, selection_id,
                                              KST, SEL_NAMES)
else:
    from official_result_backfill import (_official_source, _aware_timestamp, _game_kickoff,
                                          _latest_predictions, event_id, selection_id,
                                          KST, SEL_NAMES)


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid line')
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            raise ValueError('invalid line')
        return n
    except (InvalidOperation, TypeError):
        raise ValueError('invalid line') from None


def _frozen_terms(pred, game):
    p = pred.get('predictions') or {}
    options = (pred.get('features') or {}).get('options')
    if p.get('action') != 'market_reference' or not isinstance(options, list):
        raise ValueError('missing_frozen_terms')
    market, label = p.get('market'), p.get('label') or ''
    if market not in {'승패', '승무패', '승①패', '승⑤패', '핸디캡', '언더오버',
                      '전반승패', '전반승무패', '전반핸디캡', '전반언더오버'}:
        raise ValueError('unsupported_frozen_market')
    selected = [o for o in options if isinstance(o, dict) and
                o.get('selection_id') == p.get('selection_id') and o.get('offer_id') == p.get('offer_id')]
    if len(selected) != 1:
        raise ValueError('invalid_frozen_selection')
    chosen = selected[0]
    if (chosen.get('market') != market or (chosen.get('label') or '') != label or
            chosen.get('selection') != p.get('selection') or chosen.get('odds') != p.get('odds')):
        raise ValueError('invalid_frozen_selection')
    line = chosen.get('line')
    if market.endswith(('핸디캡', '언더오버')):
        try:
            line = _number(line)
        except ValueError:
            raise ValueError('missing_frozen_line') from None
        prefix = ('h ' if market.startswith('전반') else '') + ('H ' if market.endswith('핸디캡') else 'U ')
        match = re.fullmatch(re.escape(prefix) + r'([+-]?\d+(?:\.\d+)?)', label)
        if not match or _number(match.group(1)) != line:
            raise ValueError('invalid_frozen_line')
        if market.endswith('핸디캡') and abs(line % 1) not in (Decimal(0), Decimal('0.5')):
            raise ValueError('unsupported_frozen_line')
    elif line is not None:
        raise ValueError('invalid_frozen_line')
    group = [o for o in options if isinstance(o, dict) and o.get('market') == market
             and (o.get('label') or '') == label and o.get('line') == chosen.get('line')]
    names = SEL_NAMES.get((market, len(group)))
    if not names or len({o.get('selection') for o in group}) != len(group) or {o.get('selection') for o in group} != set(names):
        raise ValueError('invalid_frozen_roster')
    offer_ids = set()
    for option in group:
        option_id = {'market': market, 'label': label, 'line': option.get('line'), '선택': option['selection']}
        if (selection_id(game, option_id) != option.get('selection_id') or
                not isinstance(option.get('offer_id'), str) or
                not re.fullmatch(r'off_[0-9a-f]{16}', option['offer_id'])):
            raise ValueError('invalid_frozen_roster')
        offer_ids.add(option['offer_id'])
    if len(offer_ids) != len(group):
        raise ValueError('invalid_frozen_roster')
    return market, label, line, names, names.index(chosen['selection'])


def _physical_score(row, market):
    raw = row['raw_result_text']
    if market.endswith('언더오버'):
        if not re.fullmatch(r'\d+(?:\.\d+)?', raw):
            raise ValueError('invalid_official_score')
        return (_number(raw),)
    match = re.fullmatch(r'(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?)', raw)
    if not match:
        raise ValueError('invalid_official_score')
    home, away = _number(match.group(1)), _number(match.group(2))
    if market.endswith('핸디캡'):
        m = re.fullmatch(r'(?:h )?H ([+-]?\d+(?:\.\d+)?)', row.get('label') or '')
        if not m:
            raise ValueError('invalid_official_line')
        home -= _number(m.group(1))
    if home < 0 or away < 0 or home % 1 or away % 1:
        raise ValueError('invalid_official_score')
    return home, away


def _target_outcome(market, line, names, selected_index, score):
    if market.endswith('언더오버'):
        comparison = score[0] - line
        if comparison == 0:
            raise ValueError('ambiguous_tie')
        winner, label = (0, '언더') if comparison < 0 else (1, '오버')
    else:
        diff = score[0] - score[1]
        if market.endswith('핸디캡'):
            diff += line
            if diff == 0 and len(names) == 2:
                raise ValueError('ambiguous_tie')
            winner = 0 if diff > 0 else len(names) - 1 if diff < 0 else 1
            label = '핸디승' if diff > 0 else '핸디패' if diff < 0 else '핸디무'
        elif market in {'승①패', '승⑤패'}:
            if diff == 0:
                raise ValueError('ambiguous_tie')
            threshold = 1 if market == '승①패' else 5
            winner = 1 if abs(diff) <= threshold else 0 if diff > 0 else 2
            label = ('①' if threshold == 1 else '⑤') if winner == 1 else '홈승' if winner == 0 else '홈패'
        else:
            if diff == 0 and len(names) == 2:
                raise ValueError('ambiguous_tie')
            winner = 0 if diff > 0 else len(names) - 1 if diff < 0 else 1
            label = '홈승' if diff > 0 else '홈패' if diff < 0 else '무승부'
    return ('hit' if winner == selected_index else 'miss'), label


def plan_frozen_terms_from_official_scores(records, officialrows, *, snapshot_ids, observed_at, source):
    """Build a read-only plan; official rows must have validated DOM evidence.

    The original game number cannot be reconstructed from offer hashes. The
    caller must not describe this result as a verified sale or original offer.
    """
    observed = _aware_timestamp(observed_at)
    if observed is None or not isinstance(source, dict) or not source.get('name') or not source.get('provenance'):
        raise ValueError('timezone-aware observed_at and named source provenance required')
    by_event = {}
    for row in officialrows:
        try:
            game = {**row, 'year': int(row['year']), 'round': int(row['round'])}
            by_event.setdefault(event_id(game), []).append(row)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    latest = _latest_predictions(records)
    settled = {r['snapshot_id'] for r in records if r.get('record_type') == 'settlement'}
    selected_ids = set(snapshot_ids)
    entries, holds = [], []
    for event, pred in latest.items():
        sid = pred['snapshot_id']
        if sid not in selected_ids:
            continue
        if sid in settled:
            holds.append({'snapshot_id': sid, 'reason': 'existing_settlement'})
            continue
        if (pred.get('predictions') or {}).get('action') != 'market_reference':
            holds.append({'snapshot_id': sid, 'reason': 'latest_not_selected'})
            continue
        fixture = (pred.get('features') or {}).get('fixture') or {}
        kickoff = _aware_timestamp(pred.get('kickoff'))
        candidates = []
        for row in by_event.get(event, ()):
            try:
                if row.get('market') != (pred.get('predictions') or {}).get('market'):
                    continue
                if any(fixture.get(k) is not None and fixture[k] != row.get(k)
                       for k in ('sport', 'league', 'home', 'away', 'date', 'source_event_id', 'match_id')):
                    continue
                if fixture.get('year') is not None and int(fixture['year']) != int(row['year']):
                    continue
                start = _game_kickoff(row, observed)
                if kickoff is None or start is None or start.replace(tzinfo=KST) != kickoff or kickoff > observed:
                    continue
                candidates.append(row)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        if not candidates:
            holds.append({'snapshot_id': sid, 'reason': 'no_same_fixture_market_score'})
            continue
        try:
            market, label, line, names, selected_index = _frozen_terms(pred, candidates[0])
            if candidates[0].get('sport') == 'vl' and market.endswith(('핸디캡', '언더오버')):
                raise ValueError('unsupported_score_unit')
            scores, evidence_rows, offers = set(), [], {}
            for row in candidates:
                evidence = _official_source(row, int(row['year']), int(row['round']), observed, source)
                if evidence is None:
                    raise ValueError('missing_official_source')
                offer_key = (int(row['year']), int(row['round']), str(row['game_no']))
                offer_terms = (row.get('market'), row.get('label'), row.get('n_way'), row.get('raw_result_text'))
                if offer_key in offers and offers[offer_key] != offer_terms:
                    raise ValueError('conflicting_official_offers')
                offers[offer_key] = offer_terms
                scores.add(_physical_score(row, market))
                evidence_rows.append({'official_source': evidence, 'official_round': int(row['round']),
                                      'official_game_no': str(row['game_no']),
                                      'official_line': row.get('label') or '',
                                      'official_result_text': row['raw_result_text']})
            if len(scores) != 1:
                raise ValueError('conflicting_official_scores')
            grade, target_label = _target_outcome(market, line, names, selected_index, next(iter(scores)))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            holds.append({'snapshot_id': sid, 'reason': str(exc) or 'invalid_official_source'})
            continue
        evidence_rows.sort(key=lambda e: (e['official_round'], e['official_game_no'], e['official_source']['row_index']))
        outcome = {'result': grade, 'selection_id': pred['predictions']['selection_id'], 'official_result': target_label}
        version = 'official-' + hashlib.sha256(json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
        entries.append({'event_id': event, 'snapshot_id': sid, 'outcome': outcome,
                        'settled_at': observed.isoformat(), 'settlement_version': version,
                        'source': {**source, 'mode': 'official_score_with_frozen_terms',
                                   'original_market': market, 'original_label': label,
                                   'original_line': str(line) if line is not None else None,
                                   'original_n_way': len(names), 'official_scores': evidence_rows}})
    found = {p['snapshot_id'] for p in latest.values()}
    holds.extend({'snapshot_id': sid, 'reason': 'not_latest_or_missing'} for sid in sorted(selected_ids - found))
    return {'entries': entries, 'holds': holds}


def apply_frozen_terms_from_official_scores(ledger, plan, officialrows, *, observed_at, source):
    """Rebuild and compare the complete plan under the standard ledger lock."""
    entries = plan['entries']
    with ledger.verified_session():
        checked = plan_frozen_terms_from_official_scores(
            ledger.records(), officialrows, snapshot_ids=[e['snapshot_id'] for e in entries],
            observed_at=observed_at, source=source)
        if checked['holds'] or checked['entries'] != entries:
            raise ValueError('backfill plan stale or changed; nothing appended')
        return ledger.append_settlements([{k: v for k, v in entry.items() if k != 'event_id'}
                                          for entry in entries])
