"""Normalize a captured Betman closed-slip DOM without fetching or writing data."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
SPORTS = {'축구': 'sc', '야구': 'bs', '농구': 'bk', '배구': 'vl'}
WEEKDAYS = '월화수목금토일'


class Rejected(ValueError):
    pass


def _document_identity(document):
    selected = document.get('selectedRound')
    if not isinstance(selected, list) or len(selected) != 1 or not isinstance(selected[0], dict):
        raise ValueError('expected one selected round')
    value = selected[0].get('value', '')
    match = re.fullmatch(r'(G101),(\d{2})(\d{4})', value)
    url = urlsplit(document.get('url', ''))
    query = parse_qs(url.query, keep_blank_values=True)
    if (not match or url.scheme != 'https' or url.hostname != 'www.betman.co.kr'
            or url.path != '/main/mainPage/gamebuy/closedGameSlipIFR.do'
            or query.get('gmId') != [match.group(1)]
            or query.get('gmTs') != [match.group(2) + match.group(3)]):
        raise ValueError('selected round and Betman URL do not agree')
    try:
        received = datetime.fromisoformat(document['received_at'].replace('Z', '+00:00'))
        if received.utcoffset() is None:
            raise ValueError
        received = received.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('received_at must be timezone-aware') from None
    if not isinstance(document.get('rows'), list):
        raise ValueError('rows must be a list')
    return int('20' + match.group(2)), int(match.group(3)), value, received


def _option_sides(row, labels):
    options = row.get('options')
    keys = ['1', '3'] if len(labels) == 2 else ['1', '2', '3']
    if not isinstance(options, list) or len(options) != len(labels):
        raise Rejected('invalid_options')
    for opt, key, side in zip(options, keys, labels):
        if not isinstance(opt, dict) or str(opt.get('key')) != key:
            raise Rejected('invalid_options')
        text = opt.get('text')
        lines = text.splitlines() if isinstance(text, str) else []
        if len(lines) < 2 or lines[1].strip() != side:
            raise Rejected('invalid_options')


def _decimal(text):
    try:
        value = Decimal(text)
        if not value.is_finite():
            raise InvalidOperation
        return value
    except (InvalidOperation, TypeError, ValueError):
        raise Rejected('invalid_result') from None


def _row(row, year, rnd, received):
    if not isinstance(row, dict):
        raise Rejected('invalid_row')
    if row.get('result_action') != '결과보기':
        raise Rejected('not_completed')
    try:
        raw_time = row['kickoff_text']
        if not isinstance(raw_time, str) or not re.fullmatch(r'\d{14}', raw_time):
            raise ValueError
        kickoff = datetime.strptime(raw_time, '%Y%m%d%H%M%S').replace(tzinfo=KST)
        suffix = re.search(r'_(\d{13})$', row['rowname'])
        if not suffix or int(suffix.group(1)) != int(kickoff.timestamp() * 1000):
            raise ValueError
        if kickoff > received:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise Rejected('invalid_kickoff') from None
    game_name = row.get('game_name')
    if not isinstance(game_name, str) or game_name.count(':') != 1:
        raise Rejected('invalid_fixture')
    home, away = game_name.split(':')
    league = row.get('league')
    game_no = row.get('game_no')
    if (not home or not away or not isinstance(league, str) or not league.strip()
            or not isinstance(game_no, str) or not re.fullmatch(r'\d+', game_no)):
        raise Rejected('invalid_fixture')
    raw_market = row.get('market_text')
    if not isinstance(raw_market, str) or ' ' not in raw_market:
        raise Rejected('unsupported_market')
    sport_name, kind = raw_market.split(' ', 1)
    if sport_name not in SPORTS:
        raise Rejected('unsupported_market')
    half = kind.startswith('전반 ')
    if half:
        kind = kind[3:]
    families = {'승패': ('승패', ['승', '패']), '승무패': ('승무패', ['승', '무', '패']),
                '핸디캡': ('핸디캡', None), '소수핸디캡': ('핸디캡', None),
                '세트핸디캡': ('핸디캡', None), '언더오버': ('언더오버', ['언더', '오버']),
                '승1패': ('승①패', ['승', '1', '패']), '승5패': ('승⑤패', ['승', '5', '패'])}
    if kind not in families or (half and kind in {'승1패', '승5패', '세트핸디캡'}):
        raise Rejected('unsupported_market')
    if kind == '세트핸디캡' and sport_name != '배구':
        raise Rejected('unsupported_market')
    if ((kind == '승1패' and sport_name != '야구') or
            (kind == '승5패' and sport_name not in {'농구', '배구'})):
        raise Rejected('unsupported_market')
    family, sides = families[kind]
    raw_label = row.get('label')
    if not isinstance(raw_label, str):
        raise Rejected('invalid_label')
    label = ''
    line = None
    if family in {'핸디캡', '언더오버'}:
        pattern = r'H ([+-]?\d+\.\d+)' if family == '핸디캡' else r'U/O (\d+\.\d+)'
        m = re.fullmatch(pattern, raw_label)
        if not m:
            raise Rejected('invalid_label')
        line = _decimal(m.group(1))
        if family == '핸디캡':
            if line % 1 not in (Decimal('0'), Decimal('0.5'), Decimal('-0.5')):
                raise Rejected('unsupported_fractional_handicap')
            if kind == '소수핸디캡' and abs(line % 1) != Decimal('0.5'):
                raise Rejected('unsupported_fractional_handicap')
            sides = ['승', '무', '패'] if len(row.get('options') or []) == 3 else ['승', '패']
            if abs(line % 1) == Decimal('0.5') and len(sides) != 2:
                raise Rejected('invalid_options')
            label = 'H ' + m.group(1)
        else:
            label = 'U ' + m.group(1)
    elif raw_label:
        raise Rejected('invalid_label')
    elif family == '승①패':
        label = '승①패'
    if half:
        label = 'h(전반)' if family in {'승패', '승무패'} else 'h ' + label
        family = '전반' + family
    _option_sides(row, sides)
    raw_result = row.get('result_text')
    if not isinstance(raw_result, str):
        raise Rejected('invalid_result')
    if family.endswith('언더오버'):
        if not re.fullmatch(r'\d+(?:\.\d+)?', raw_result):
            raise Rejected('invalid_result')
        total = _decimal(raw_result)
        if total % 1:
            raise Rejected('invalid_result')
        if total == line:
            raise Rejected('ambiguous_tie')
        result = '언더' if total < line else '오버'
    else:
        m = re.fullmatch(r'(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?)', raw_result)
        if not m:
            raise Rejected('invalid_result')
        left, right = _decimal(m.group(1)), _decimal(m.group(2))
        if family in {'승①패', '승⑤패'}:
            if left < 0 or right < 0 or left % 1 or right % 1 or left == right:
                raise Rejected('ambiguous_tie')
            threshold = 1 if family == '승①패' else 5
            result = ('①' if threshold == 1 else '⑤') if abs(left - right) <= threshold else ('홈승' if left > right else '홈패')
        elif family.endswith('핸디캡'):
            unadjusted_home = left - line
            if unadjusted_home < 0 or unadjusted_home % 1 or right < 0 or right % 1:
                raise Rejected('invalid_result')
            result = '핸디승' if left > right else '핸디패' if left < right else '핸디무'
            if left == right and len(sides) != 3:
                raise Rejected('ambiguous_tie')
        else:
            if left < 0 or right < 0 or left % 1 or right % 1:
                raise Rejected('invalid_result')
            if left == right and len(sides) != 3:
                raise Rejected('ambiguous_tie')
            result = '홈승' if left > right else '홈패' if left < right else '무승부'
    return {'year': year, 'round': rnd, 'game_no': game_no, 'date': kickoff.strftime('%m.%d') + f'({WEEKDAYS[kickoff.weekday()]}) ' + kickoff.strftime('%H:%M'),
            'sport': SPORTS[sport_name], 'league': league, 'home': home, 'away': away,
            'market': family, 'label': label, 'n_way': len(sides), 'result': result,
            'raw_market_scope': raw_market, 'raw_result_text': raw_result, 'raw_label': raw_label}


def normalize_betman_result_dom(document, *, evidence_sha256):
    """Return mapped rows, rejected rows/reason counts, and auditable DOM evidence."""
    if not isinstance(evidence_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', evidence_sha256):
        raise ValueError('evidence_sha256 must be the lowercase capture file SHA-256')
    year, rnd, selected, received = _document_identity(document)
    digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()
    evidence = {'name': 'betman_closed_slip_dom', 'provenance': 'captured_betman_website_dom',
                'url': document['url'], 'selected_round': selected,
                'received_at': received.isoformat(), 'evidence_sha256': evidence_sha256,
                'document_sha256_canonical_json': digest}
    mapped, rejected = [], []
    for index, row in enumerate(document['rows']):
        try:
            normalized_row = _row(row, year, rnd, received)
            official_source = {'url': document['url'], 'evidence_sha256': evidence_sha256,
                               'received_at': received.isoformat(),
                               'result_text': row['result_text'], 'result_action': row['result_action'],
                               'market_text': row['market_text'],
                               'label': row['label'], 'row_index': index,
                               'kickoff_text': row['kickoff_text'], 'rowname': row['rowname'],
                               'game_name': row['game_name'], 'game_no': row['game_no'],
                               'league': row['league'],
                               'options': [{'key': opt['key'], 'side': opt['text'].splitlines()[1].strip()}
                                           for opt in row['options']]}
            mapped.append({**normalized_row, 'dom_row_index': index,
                           'dom_evidence': evidence, 'official_source': official_source})
        except Rejected as exc:
            rejected.append({'row_index': index, 'reason': str(exc), 'row': row})
    return {'mapped_rows': mapped, 'rejected': rejected,
            'rejected_counts': dict(Counter(item['reason'] for item in rejected)), 'source': evidence}
