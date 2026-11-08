"""Settle changed lines with sealed terms and confirmed, unadjusted Proto scores.

Only baseball/basketball full-game products qualify. Soccer score periods are
not proven by match_results and remain outside this recovery path. No network,
model fitting, prediction edits, new jobs, or direct ledger writes occur here.
"""
from datetime import datetime, timedelta
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import math
import re
import sqlite3
from zoneinfo import ZoneInfo

from ai_decision import event_id, selection_id, offer_id
from bets import SEL_NAMES, winner_index

RULE_URL = 'https://www.sportstoto.co.kr/proto_intro'
KST = ZoneInfo('Asia/Seoul')


class _ReadOnlyScores:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def _time(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def confirmed_score(database, fixture, start, observed):
    """Read exact physical identity, then check latest versions by source key.

    A newer invalidation may have blank teams/kickoff; never filter it out and
    accidentally reuse an older confirmed score. Never read the whole archive.
    """
    local_start = start.astimezone(KST).replace(tzinfo=None).isoformat()
    with database.connect() as connection:
        connection.execute('BEGIN')
        keys = [tuple(r) for r in connection.execute('''SELECT DISTINCT season,round,game_no FROM match_results
            WHERE kickoff=? AND league=? AND sport=? AND home_team=? AND away_team=?
              AND source='proto' AND season=? LIMIT 9''',
            (local_start, fixture['league'], fixture['sport'], fixture['home'], fixture['away'],
             fixture['year']))]
        if not keys or len(keys) > 8:
            return None
        rows = []
        for season, round_no, number in keys:
            row = connection.execute('''SELECT * FROM match_results
                WHERE source='proto' AND season=? AND round=? AND game_no=?
                ORDER BY observed_at DESC LIMIT 1''',
                (season, round_no, number)).fetchone()
            row = dict(row)
            if (row['result_state'] != 'confirmed' or row['kickoff'] != local_start
                    or any(row[k] != fixture[f] for k, f in (
                        ('league','league'), ('sport','sport'), ('home_team','home'), ('away_team','away')))
                    or not start <= _time(row['observed_at']) <= observed
                    or any(type(row[k]) is not int or row[k] < 0 for k in ('home_score','away_score'))):
                return None
            rows.append(row)
    if len({(r['home_score'], r['away_score']) for r in rows}) != 1:
        return None
    rows.sort(key=lambda r: (r['season'],r['round'],r['game_no']))
    return rows


def _changed_product(feed, fixture, item, terms):
    matches = [r for r in (feed.get('markets') or {}).get(str(fixture['round']), {}).values()
               if str(r.get('game_no')) == str(item['game_no'])
               and r.get('market') == item['market']
               and r.get('n_way') == terms['n_way']
               and all(r.get(k) == fixture[k] for k in ('league','sport','home','away','date'))]
    if len(matches) != 1:
        return None
    row = matches[0]
    # A current live/cancelled/ambiguous row cannot certify a final score scope.
    if winner_index(row['n_way'], row.get('result')) is None:
        return None
    pattern = r'H\s*([-+]?\d+(?:\.\d+)?)' if item['market'] == '핸디캡' else r'(?:U/O|[UO])?\s*([-+]?\d+(?:\.\d+)?)'
    old = re.fullmatch(pattern, str(item.get('label') or ''), re.I)
    new = re.fullmatch(pattern, str(row.get('label') or ''), re.I)
    if not old or not new or Decimal(old[1]) != Decimal(str(item['line'])):
        return None
    return row if Decimal(new[1]) != Decimal(old[1]) else None


def add_frozen_candidates(candidates, feed, predictions, observed, *, database=None):
    """Augment exact saved-offer keys; ordinary exact-product grades win.

    A recovered row carries explicit score provenance, not a counterfeit copy of
    the final current-line grade. Prices always come from the saved prediction.
    """
    if database is None:
        from runtime_db import database_enabled, configured_path
        if not database_enabled():
            return 0
        database = _ReadOnlyScores(configured_path())
    events = {key[0] for key in candidates}
    scores = {}; added = 0
    for event, prediction in predictions.items():
        if event not in events:
            continue
        fixture = (prediction.get('features') or {}).get('fixture') or {}
        try:
            start = _time(prediction['kickoff'])
            if (fixture.get('sport') not in {'bs', 'bk'} or event_id(fixture) != event
                    or observed < start or observed - start > timedelta(days=90)
                    or any(_time(prediction[k]) >= start - timedelta(minutes=30)
                           for k in ('as_of','captured_at','market_observed_at'))):
                continue
            items = ((prediction.get('predictions') or {}).get('market_recommendations') or {}).get('items') or []
            for item in items:
                try:
                    terms = item.get('terms') or {}
                    if (item.get('market') not in {'핸디캡','언더오버'}
                            or _time(item.get('market_observed_at', prediction['market_observed_at']))
                               > _time(prediction['captured_at'])
                            or terms.get('sport') != fixture['sport'] or terms.get('league') != fixture['league']
                            or terms.get('period') != 'full_including_extra'
                            or item.get('result_period') != terms['period'] or item.get('period') != 'full_game'
                            or item.get('settlement_rule') != terms.get('settlement_rule')
                            or terms.get('settlement_rule') not in {'total_half','handicap_half','handicap_3way'}
                            or terms.get('unit') != ('points' if fixture['sport']=='bk' else 'runs')
                            or str(terms.get('game_no')) != str(item['game_no'])
                            or Decimal(str(terms.get('line'))) != Decimal(str(item['line']))
                            or terms.get('market') != ('total' if item['market']=='언더오버' else 'handicap')):
                        continue
                    current = _changed_product(feed, fixture, item, terms)
                    if current is None:
                        continue
                    identity = (fixture['year'],fixture['round'],fixture['date'],fixture['league'],fixture['home'],fixture['away'])
                    if identity not in scores:
                        try:
                            scores[identity] = confirmed_score(database, fixture, start, observed)
                        except (OSError, sqlite3.Error):
                            scores[identity] = None
                    source_rows = scores[identity]
                    if source_rows is None:
                        continue
                    from market_outcomes import settle
                    # A stale/corrected score must not contradict the current
                    # official product grade. Both sources must agree first.
                    closing_line = re.search(r'[-+]?\d+(?:\.\d+)?', current['label'])[0]
                    closing_terms = dict(terms, line=closing_line)
                    names = SEL_NAMES.get((item['market'], terms['n_way']))
                    score = source_rows[0]
                    if not names or any(
                        settle(closing_terms, side, score['home_score'], score['away_score'],
                               result_period='full_including_extra')['state'] !=
                        ('win' if index == winner_index(current['n_way'], current['result']) else 'loss')
                        for index, side in enumerate(names)):
                        continue
                    alternatives = [item, *[dict(item, **{k: arm[k] for k in (
                        'selection','selection_id','offer_id','odds')}) for arm in (item.get('comparisons') or {}).values()
                        if all(arm.get(k) for k in ('selection','selection_id','offer_id','odds'))]]
                    for selected in alternatives:
                        key = (event, selected['selection_id'], selected['offer_id'])
                        option = {'market':item['market'], 'label':item.get('label') or '', 'line':item['line'],
                                  '선택':selected['selection'], '게임번호':str(item['game_no'])}
                        if (key in candidates or selection_id(fixture, option) != key[1]
                                or offer_id(fixture, option) != key[2]
                                or not math.isfinite(float(selected['odds'])) or float(selected['odds']) <= 1):
                            continue
                        state = settle(terms, selected['selection'], score['home_score'], score['away_score'],
                                       result_period='full_including_extra')['state']
                        status = {'win':'hit','loss':'miss','refund':'void'}.get(state)
                        if status is None:
                            continue
                        source = dict(name='proto_final_score_frozen_line', path='match_results',
                            round=str(fixture['round']), game_no=str(item['game_no']), game_date=fixture['date'],
                            original_line=item['line'], result_feed_label=current['label'],
                            final_score=[score['home_score'],score['away_score']], result_period='full_including_extra',
                            rule_source=RULE_URL, score_records=source_rows,
                            score_sha256=hashlib.sha256(json.dumps(source_rows,sort_keys=True,ensure_ascii=False,
                                                                  separators=(',',':')).encode()).hexdigest())
                        derived = dict(current, _frozen_score_source=source)
                        derived.pop('result', None)  # Never relabel the closing-line grade as an old-line result.
                        candidates[key] = [(status, derived, str(fixture['round']))]
                        added += 1
                except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error):
                    continue  # Incomplete legacy terms keep the existing hold.
        except (ValueError, TypeError, KeyError, ArithmeticError):
            continue
    return added


def settlement_source(row, round_no):
    if row.get('_frozen_score_source'):
        return row['_frozen_score_source']
    return {'name':'proto_official', 'round':round_no, 'game_no':row.get('game_no'),
            'game_date':row.get('date'), 'path':'live_odds'}
