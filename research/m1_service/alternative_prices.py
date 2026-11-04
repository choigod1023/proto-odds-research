"""Research-only Betman total-price adapter; no I/O or collector changes.

The caller supplies the exact detail bytes and the collector's actual body-complete
receipt and listing metadata. Historical receipts must never be backdated.
"""
import hashlib
import json
import math
from collections import Counter
from datetime import timedelta

from research.kbo_forward.prices import DETAIL_URL, KST, TEAMS, _millis, _moment, _price

TOTAL = ('79', '7', '야구 언더오버', '일반 언더오버')
HANDICAP = ('7', '5', '야구 핸디캡', '일반 소수핸디캡')


def parse_detail(raw, round_info, games, receipt, evidence, as_of):
    """Return review-compatible offers and exclusions from one G101 detail.

    as_of is the actual decision time, never earlier than body completion. The
    caller must authenticate receipt provenance; a SHA proves bytes, not time.
    Duplicate markets for a fixture are all withheld. This is not an order.
    """
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != evidence.get('sha256'):
        raise ValueError('source_sha256_mismatch')
    if evidence.get('uri') != DETAIL_URL or _moment(evidence['received_at']) != _moment(receipt):
        raise ValueError('source_receipt_mismatch')
    received, decision = _moment(receipt), _moment(as_of)
    if received > decision:
        raise ValueError('receipt_after_decision')
    data = json.loads(raw)
    ts = str(round_info['gmTs'])
    if round_info.get('gmId') != 'G101' or len(ts) != 6 or not ts.isascii() or not ts.isdigit() or str(data.get('gmTs')) != ts:
        raise ValueError('round_mismatch')
    opening, closing = _millis(round_info['saleStartDate']), _millis(round_info['saleEndDate'])
    if opening >= closing:
        raise ValueError('invalid_round_sale_window')
    schedules = data['compSchedules']; keys, rows = schedules['keys'], schedules['datas']
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys) or len(keys) != len(set(keys)) or not isinstance(rows, list):
        raise ValueError('invalid_detail_envelope')
    if any(not isinstance(r, list) or len(r) != len(keys) for r in rows):
        raise ValueError('invalid_detail_row')
    decoded = [dict(zip(keys, row)) for row in rows]
    sequences = Counter(r.get('matchSeq') for r in decoded if type(r.get('matchSeq')) is int)
    fixtures = list(games)
    offers, excluded = [], []
    for row in decoded:
        if row.get('leagueCode') != 'BS001':
            continue
        product = tuple(row.get(k) for k in ('betId', 'betTypId', 'betNm', 'betTypNm'))
        if product not in (TOTAL, HANDICAP):
            continue
        market_id = f"G101:{ts}:{row.get('matchSeq')}"
        try:
            if product == HANDICAP:
                raise ValueError('handicap_draw_settlement_unverified')
            if type(row.get('matchSeq')) is not int or row['matchSeq'] <= 0 or sequences[row['matchSeq']] != 1:
                raise ValueError('invalid_or_duplicate_market_id')
            if (row.get('winTxt'), row.get('drawTxt'), row.get('loseTxt')) != ('언더', '-', '오버') or row.get('drawAllot') != 0 or row.get('gameSubject') not in (None, '') or row.get('drawHandi') != 0:
                raise ValueError('product_terms_mismatch')
            line = row.get('winHandi')
            if type(line) not in (int, float) or not math.isfinite(line) or line <= 0 or line * 2 != int(line * 2) or int(line * 2) % 2 != 1 or row.get('loseHandi') != line:
                raise ValueError('unsupported_or_mismatched_line')
            if row.get('gameReject') != '0' or row.get('buyReject') != '0' or row.get('protoStatus') != '2' or row.get('live') != 'N' or row.get('unsetSchedule') != 'N' or row.get('unsetEndDate') != 'N':
                raise ValueError('not_confirmed_on_sale')
            if row.get('homeId') not in TEAMS or row.get('awayId') not in TEAMS or row['homeId'] == row['awayId'] or not all(isinstance(row.get(k), str) and row[k].strip() for k in ('homeName', 'awayName')):
                raise ValueError('invalid_teams')
            start, close = _millis(row['gameDate']), _millis(row['endDate'])
            if not (opening <= received <= decision < close <= start and decision < closing and decision <= start - timedelta(minutes=30)):
                raise ValueError('late_or_closed')
            matches = [g for g in fixtures if g.get('home_id') == row['homeId'] and g.get('away_id') == row['awayId'] and _moment(g['kickoff_at']) == start and g.get('season') == start.astimezone(KST).year and isinstance(g.get('official_game_id'), str) and g['official_game_id'].strip()]
            if len(matches) != 1 or sum(g.get('official_game_id') == matches[0]['official_game_id'] for g in fixtures) != 1:
                raise ValueError('fixture_unmatched_or_ambiguous')
            prices = (_price(row['winAllot']), _price(row['loseAllot']))
            fixture = matches[0]['official_game_id']
            common = {'fixture_id': fixture, 'period': 'full_game', 'kind': 'full_game_total', 'line': line, 'settlement_rule': 'push_refund', 'market_id': market_id, 'received_at': received.isoformat(), 'as_of': decision.isoformat(), 'source_sha256': evidence['sha256'], 'evidence': dict(evidence), 'source_product': dict(zip(('betId','betTypId','betNm','betTypNm'), product)), 'original_draw_refund': False, 'research_only': True}
            offers.extend(dict(common, offer_id=f'{market_id}:{side}', selection=side, odds=price) for side, price in zip(('under', 'over'), prices))
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            excluded.append({'market_id': market_id, 'reason': str(exc) or 'invalid_field'})
    counts = Counter(o['fixture_id'] for o in offers if o['selection'] == 'under')
    duplicates = {f for f, n in counts.items() if n > 1}
    for offer in offers:
        if offer['fixture_id'] in duplicates and offer['selection'] == 'under':
            excluded.append({'market_id': offer['market_id'], 'reason': 'multiple_market_candidates'})
    return [o for o in offers if o['fixture_id'] not in duplicates], excluded
