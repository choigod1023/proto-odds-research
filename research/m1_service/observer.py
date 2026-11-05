"""Event-driven M1 rehearsal attached to the existing verified collector.

Separate append-only research ledger; no wagers, production picks, training,
scheduler, or current paper-wallet writes. Never backfill a pregame capture.
"""
from copy import deepcopy
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from research.kbo_forward.locking import single_writer
from research.m1_service.model import forecast, metadata
from research.m1_service.low_odds import review


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def moment(value):
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.utcoffset() is None:
        raise ValueError('explicit UTC offset required')
    return d.astimezone(timezone.utc)


def read(path):
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('incomplete M1 ledger; do not truncate')
    records, prior = [], '0'*64
    for line in raw.splitlines():
        row = json.loads(line); claimed = row.pop('hash')
        if row['previous_hash'] != prior or digest(canonical(row)) != claimed:
            raise ValueError('M1 ledger hash chain differs')
        row['hash'] = claimed; records.append(row); prior = claimed
    return records


def _append(path, make):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with single_writer(path.with_suffix('.append.lock')) as owned:
        if not owned:
            raise ValueError('M1 observer already writing')
        rows = read(path); row = make(rows)
        if row is None:
            return None
        row['previous_hash'] = rows[-1]['hash'] if rows else '0'*64
        row['hash'] = digest(canonical(row)); raw = canonical(row)+b'\n'
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
        try:
            if os.write(fd, raw) != len(raw):
                raise OSError('incomplete M1 append; requires review')
            os.fsync(fd)
        finally:
            os.close(fd)
        return row


def _event_key(event, market):
    return digest(canonical({'game': event['official_game_id'], 'season': event['season'],
                             'market_id': market['market_id'] if market else None}))


def _verify_archives(payload, root, now):
    from research.kbo_forward import official, features
    if root is None or not Path(root).is_dir():
        raise ValueError('verified collector archive root required')
    base = Path(root).resolve(); bundles = {}
    verifier = official.OfficialClient.__new__(official.OfficialClient)
    verifier.root = base
    def no_network(*args, **kwargs):
        raise ValueError('offline source verification cannot fetch')
    verifier.fetch = no_network
    for kind in ('announcement', 'history'):
        evidence = payload['evidence'][kind]
        body = evidence['body']
        if 'archived_canonical_bundle_sha256' not in body:
            raise ValueError('preserved raw source bundle required')
        path = (base/body['relative_file']).resolve()
        wanted = body['archived_canonical_bundle_sha256']
        if not re.fullmatch('[0-9a-f]{64}', str(wanted)):
            raise ValueError('invalid archive digest')
        if not path.is_relative_to(base/'verified-bundles') or path.name != wanted+'.json.gz':
            raise ValueError('source archive outside collector root')
        raw = gzip.decompress(path.read_bytes())
        if digest(raw) != wanted:
            raise ValueError('source archive differs')
        bundles[kind] = json.loads(raw)
        responses = bundles[kind].get('responses') or []
        if not responses:
            raise ValueError('raw HTTP source responses required')
        for response in responses:
            rec = response['receipt']
            if rec['url'] not in (official.SCHEDULE, official.LINEUP, official.BOX, official.PROFILE,
                                   official.SEARCH, official.RETIRE_PITCHER, official.RETIRE_HITTER):
                raise ValueError('unapproved official source endpoint')
            if moment(rec['requested_at']) > moment(rec['finished_at']) or moment(rec['finished_at']) > now:
                raise ValueError('source response clock differs')
            disk = verifier.verified_body(rec, rec['url'])
            if base64.b64decode(response['body_base64'], validate=True) != disk:
                raise ValueError('archive response bytes differ from collector cache')
    ann = bundles['announcement']['responses']; event = payload['event']
    schedules = [response for response in ann if response['receipt']['url'] == official.SCHEDULE]
    games = [g for response in schedules for g in json.loads(base64.b64decode(response['body_base64']))['game']
             if g.get('G_ID') == event['official_game_id']]
    if len(games) != 1:
        raise ValueError('unique announced official game required')
    game = games[0]
    expected_start = datetime.strptime(game['G_DT']+' '+game['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=official.KST)
    if (str(game['HOME_ID']) != event['home_id'] or str(game['AWAY_ID']) != event['away_id']
            or str(game['SEASON_ID']) != str(event['season']) or expected_start != moment(event['kickoff_at'])
            or str(game['GAME_STATE_SC']) != '1' or str(game['CANCEL_SC_ID']) != '0'):
        raise ValueError('announced fixture identity or state differs')
    lineups = [response for response in ann if response['receipt']['url'] == official.LINEUP
               and str(response['receipt']['params'].get('gameId')) == game['G_ID']]
    if len(lineups) != 1:
        raise ValueError('unique confirmed raw lineup required')
    ordered = official.parse_lineup(base64.b64decode(lineups[0]['body_base64']), game)
    if ordered is None:
        raise ValueError('raw source lineup is unconfirmed')
    # Duplicate cache receipts for an identical search are not extra evidence.
    receipts = list({digest(canonical(r['receipt'])): r['receipt'] for r in ann}.values())
    for side, prefix in (('home', 'B'), ('away', 'T')):
        pid = str(game.get(prefix+'_PIT_P_ID') or '')
        if (game.get('START_PIT_CK') not in (1, True) or not pid.isdigit() or int(pid) <= 0
                or not str(game.get(prefix+'_PIT_P_NM') or '').strip()
                or pid != payload['row']['roster'][side]['starter']):
            raise ValueError('announced starter ID differs')
        player_ids = [verifier.resolve_identity(p['name'], game[side.upper()+'_NM'], 'hitter', game['SEASON_ID'], identity_receipts=receipts)[0]
                      for p in ordered[side]]
        if player_ids != payload['row']['roster'][side]['hitters']:
            raise ValueError('announced ordered hitter IDs differ')
    history = bundles['history'].get('normalized_history')
    if not history or not verifier.verify_history_stats(event, payload['row']['roster'], history):
        raise ValueError('raw historical source statistics do not reconstruct')
    rebuilt = features.build_row(event, payload['row']['roster'], history, now.isoformat())
    for key in ('features', 'raw_features', 'history_coverage'):
        if payload['row'].get(key) != rebuilt[key]:
            raise ValueError('model input differs from archived history: '+key)


def _verify_market_bytes(payload, root):
    from research.kbo_forward.prices import DETAIL_URL, parse_detail
    if root is None:
        raise ValueError('verified price cache required')
    root = Path(root); evidence = payload['evidence']['odds']; market = payload['market']
    if evidence['uri'] != DETAIL_URL:
        raise ValueError('direct official price URL required')
    if any(not re.fullmatch('[0-9a-f]{64}', str(evidence[k])) for k in ('raw_sha256', 'round_listing_sha256')):
        raise ValueError('invalid price cache digest')
    raw = (root/(evidence['raw_sha256']+'.body')).read_bytes()
    listing = (root/(evidence['round_listing_sha256']+'.body')).read_bytes()
    if digest(raw) != evidence['raw_sha256'] or digest(listing) != evidence['round_listing_sha256'] or json.loads(raw) != evidence['body']:
        raise ValueError('price response cache differs')
    parts = market['market_id'].split(':')
    if len(parts) != 3:
        raise ValueError('invalid price market identity')
    rounds = [r for r in json.loads(listing)['protoGames'] if r.get('gmId') == 'G101' and str(r.get('gmTs')) == parts[1]]
    if len(parts) != 3 or len(rounds) != 1:
        raise ValueError('price round not in cached listing')
    quotes, _ = parse_detail(raw, rounds[0], [payload['event']], evidence['received_at'], {})
    matched = [q for q in quotes if q['market_id'] == market['market_id']]
    if len(matched) != 1 or any(matched[0][key] != value for key, value in market.items()):
        raise ValueError('model market differs from raw price response')


class Recorder:
    """Only the collector supplies its existing strict model-snapshot validator.

    A Python validator is an internal dependency, never a source-controlled flag.
    Source clocks are local collector evidence, not independent certification.
    """
    def __init__(self, path, validator, *, archive_root=None, price_archive_root=None,
                 clock=lambda: datetime.now(timezone.utc), infer=forecast):
        self.path = Path(path); self.validator = validator; self.root = archive_root
        self.price_root = price_archive_root
        self.clock = clock; self.infer = infer

    def has_capture(self, event, market_id=None):
        return any(r['type'] == 'capture' and r['input']['event']['official_game_id'] == event['official_game_id']
                   and r['input']['event']['season'] == event['season']
                   and (r['input'].get('market') or {}).get('market_id') == market_id for r in read(self.path))

    def capture(self, payload, *, unpriced=False):
        start = self.clock()
        if start < moment(metadata()['all_coefficients_preserved_by']):
            raise ValueError('capture predates preserved coefficient artifact')
        self.validator(payload, start, unpriced=unpriced)
        if not unpriced and start-moment(payload['evidence']['odds']['received_at']) > timedelta(minutes=10):
            raise ValueError('price receipt stale at M1 capture')
        _verify_archives(payload, self.root, start)
        if not unpriced:
            _verify_market_bytes(payload, self.price_root)
        row = payload['row']
        if any(row.get('history_coverage', {}).get(side, {}).get('status') != 'complete' for side in ('home', 'away')):
            raise ValueError('complete pregame history coverage required')
        for side in ('home', 'away'):
            hitters = row['roster'][side]['hitters']
            if not isinstance(hitters, list) or len(hitters) != 9 or len(set(hitters)) != 9 or not row['roster'][side]['starter']:
                raise ValueError('announced nine IDs and named starter required')
        forecasts = self.infer(row); meta = metadata()
        if set(forecasts) != set(meta['models']):
            raise ValueError('all frozen comparison models required')
        for prediction in forecasts.values():
            q = prediction['probabilities_3way']; two = prediction['probabilities_2way']
            if (prediction['fixture_id'] != row['fixture_id'] or prediction['event_date'] != row['event_date']
                    or set(q) != {'home', 'draw', 'away'} or set(two) != {'home', 'away'}
                    or any(not 0 <= p <= 1 for p in [*q.values(), *two.values()])
                    or abs(sum(q.values())-1) > 1e-9 or abs(sum(two.values())-1) > 1e-9
                    or q['home']+q['away'] <= 0
                    or abs(two['home']-q['home']/(q['home']+q['away'])) > 1e-9):
                raise ValueError('inconsistent frozen forecast identity or probabilities')
        end = self.clock(); kickoff = moment(payload['event']['kickoff_at'])
        if end < start or end > kickoff-timedelta(minutes=30):
            raise ValueError('M1 inference completed after T-30 or clock moved backwards')
        market = None if unpriced else payload['market']
        if market and any(end >= moment(market[k]) for k in ('close_at', 'round_close_at')):
            raise ValueError('market closed during M1 inference')
        choices = {}
        low_odds = None
        for name, f in forecasts.items():
            choices[name] = None
            if market:
                probs = f['probabilities_2way'] if market['kind'] == 'full_game_2way' else f['probabilities_3way']
                best = max(probs.values()); ties = [s for s, p in probs.items() if abs(p-best) <= 1e-12]
                choices[name] = ties[0] if len(ties) == 1 else None
        if market and choices.get('m1_projected'):
            offers = [{'offer_id': market['market_id']+':'+side, 'fixture_id': row['fixture_id'],
                       'period': 'full_game', 'kind': market['kind'], 'selection': side,
                       'settlement_rule': market['settlement_rule'], 'odds': float(odds)}
                      for side, odds in market['odds'].items()]
            low_odds = review(forecasts['m1_projected'], offers, market['market_id']+':'+choices['m1_projected'])
        original = deepcopy(payload); key = _event_key(payload['event'], market)
        record = {'type': 'capture', 'schema': 'm1-pregame-rehearsal-v1', 'event_key': key,
                  'captured_at': end.isoformat(), 'input': original, 'input_sha256': digest(canonical(original)),
                  'models': forecasts, 'choices': choices, 'model_metadata': meta,
                  'low_odds_review': low_odds, 'roi_eligible': not unpriced,
                  'source_scope': 'archived local HTTP bytes checked; roster, history features and prices reconstructed; no external timestamp authentication',
                  'external_source_authentication': False, 'production_policy_changed': False,
                  'paper_wallet_changed': False, 'promoted': False}
        return _append(self.path, lambda rows: None if any(r['type'] == 'capture' and r['event_key'] == key for r in rows) else record)

    def settle(self, result):
        now = self.clock(); ev = result['evidence']; uri = urlparse(ev['uri'])
        if uri.scheme != 'https' or uri.hostname not in ('koreabaseball.com', 'www.koreabaseball.com'):
            raise ValueError('official result source required')
        if digest(canonical(ev['body'])) != ev['sha256'] or moment(ev['received_at']) > now:
            raise ValueError('result receipt invalid')
        raw_games = ev['body'].get('game', [])
        matched = [g for g in raw_games if g.get('G_ID') == result['official_game_id']]
        if len(matched) != 1:
            raise ValueError('unique official result body identity required')
        g = matched[0]
        if str(g['HOME_ID']) != result['home_id'] or str(g['AWAY_ID']) != result['away_id'] or g['G_DT'] != result['game_date'].replace('-', '') or str(g['SEASON_ID']) != str(result['season']):
            raise ValueError('official result identity differs')
        status = result.get('status', 'final')
        if status == 'final':
            h, a = result['home_score'], result['away_score']
            if any(type(v) is not int or v < 0 for v in (h, a)) or (int(g['B_SCORE_CN']), int(g['T_SCORE_CN'])) != (h, a):
                raise ValueError('official score differs')
            if str(g['GAME_STATE_SC']) != '3' or str(g['GAME_RESULT_CK']) != '1' or str(g['CANCEL_SC_ID']) != '0':
                raise ValueError('official game is not final')
        elif status == 'cancelled':
            if str(g['CANCEL_SC_ID']) == '0' or '취소' not in str(g.get('CANCEL_SC_NM', '')):
                raise ValueError('official cancellation required')
            h = a = None
        else:
            raise ValueError('unsupported result status')
        captures = [r for r in read(self.path) if r['type'] == 'capture'
                    and r['input']['event']['official_game_id'] == result['official_game_id']
                    and r['input']['event']['season'] == result['season']]
        written = []
        for c in captures:
            event = c['input']['event']
            if any(event[k] != result[k] for k in ('official_game_id', 'season', 'home_id', 'away_id')) or c['input']['row']['event_date'] != result['game_date']:
                raise ValueError('result differs from frozen capture identity')
            if status == 'final' and (now < moment(event['kickoff_at']) or moment(ev['received_at']) < moment(event['kickoff_at'])):
                raise ValueError('result precedes kickoff')
            profits, outcomes = {}, {}
            winner = None if h is None else 'home' if h > a else 'away' if a > h else 'draw'
            for name, f in c['models'].items():
                q = f['probabilities_3way']; top = max(q.values())
                ties = [s for s, p in q.items() if abs(p-top) <= 1e-12]
                predicted = ties[0] if len(ties) == 1 else None
                outcomes[name] = {'selection': predicted, 'hit': predicted == winner if winner and predicted else None}
                if c['roi_eligible']:
                    market = c['input']['market']; pick = c['choices'][name]
                    refund = status == 'cancelled' or winner == 'draw' and market['kind'] == 'full_game_2way'
                    net = None if pick is None else Decimal(0) if refund else Decimal(str(market['odds'][pick]))-1 if pick == winner else Decimal(-1)
                    profits[name] = {'selection': pick, 'net_units': str(net) if net is not None else None,
                                     'refund': refund, 'hit': pick == winner if pick is not None and not refund else None}
            economics = {'status': status, 'home_score': h, 'away_score': a, 'profits': profits, 'outcomes': outcomes}
            def make(rows):
                prior = next((r for r in reversed(rows) if r['type'] == 'settlement' and r['event_key'] == c['event_key']), None)
                observed=[r for r in rows if r.get('event_key')==c['event_key'] and r['type'] in ('settlement','result_observation')]
                latest_received=max((moment(r['evidence']['received_at']) if r['type']=='settlement' else moment(r['received_at']) for r in observed),default=None)
                if latest_received is not None and moment(ev['received_at']) < latest_received:
                    raise ValueError('result correction receipt moved backwards')
                if prior and moment(ev['received_at']) == latest_received and prior['economics'] != economics:
                    raise ValueError('conflicting result at same receipt time')
                if prior and prior['economics'] == economics:
                    if moment(ev['received_at'])==latest_received:return None
                    return {'type':'result_observation','event_key':c['event_key'],'capture_hash':c['hash'],
                            'received_at':ev['received_at'],'source_sha256':ev['sha256'],
                            'economics_sha256':digest(canonical(economics)),'paper_wallet_changed':False}
                return {'type': 'settlement', 'event_key': c['event_key'], 'capture_hash': c['hash'],
                        'settled_at': now.isoformat(), 'supersedes_hash': prior['hash'] if prior else None,
                        'economics': economics, 'evidence': deepcopy(ev), 'paper_wallet_changed': False}
            r = _append(self.path, make)
            if r:
                written.append(r)
        return written

    def summary(self):
        rows = read(self.path); captures = {r['event_key']: r for r in rows if r['type'] == 'capture'}
        latest = {r['event_key']: r for r in rows if r['type'] == 'settlement'}
        methods = {}
        unpriced_accuracy = {}
        for name in metadata()['models']:
            profits = [r['economics']['profits'][name] for r in latest.values() if name in r['economics']['profits'] and r['economics']['profits'][name]['net_units'] is not None]
            decided = [p for p in profits if not p['refund']]
            net = sum((Decimal(p['net_units']) for p in profits), Decimal(0))
            methods[name] = {'bets': len(profits), 'decided': len(decided), 'hits': sum(p['hit'] for p in decided),
                             'net_units': str(net), 'roi': float(net/len(profits)) if profits else None,
                             'hit_rate': sum(p['hit'] for p in decided)/len(decided) if decided else None}
            outcomes = [r['economics']['outcomes'][name] for key, r in latest.items()
                        if not captures[key]['roi_eligible'] and r['economics']['status'] == 'final'
                        and r['economics']['outcomes'][name]['hit'] is not None]
            unpriced_accuracy[name] = {'three_way_judged': len(outcomes),
                                      'three_way_hits': sum(p['hit'] for p in outcomes),
                                      'hit_rate': sum(p['hit'] for p in outcomes)/len(outcomes) if outcomes else None,
                                      'roi': None}
        by_product = {}
        for kind in sorted({c['input']['market']['kind'] for c in captures.values() if c['roi_eligible']}):
            group = {key: c for key, c in captures.items() if c['roi_eligible'] and c['input']['market']['kind'] == kind}
            scores = {}
            for name in metadata()['models']:
                ps = [r['economics']['profits'][name] for key, r in latest.items() if key in group and r['economics']['profits'][name]['net_units'] is not None]
                decided = [p for p in ps if not p['refund']]
                net = sum((Decimal(p['net_units']) for p in ps), Decimal(0))
                scores[name] = {'bets': len(ps), 'decided': len(decided), 'hits': sum(p['hit'] for p in decided),
                                'net_units': str(net), 'roi': float(net/len(ps)) if ps else None,
                                'hit_rate': sum(p['hit'] for p in decided)/len(decided) if decided else None}
            by_product[kind] = {'capture_entries': len(group), 'models': scores,
                                'unique_fixtures': len({(c['input']['event']['season'], c['input']['event']['official_game_id']) for c in group.values()})}
        return {'captured': len(captures), 'settled': len(latest), 'methods': methods,
                'aggregate_scope': 'per-product paper entries; not independent game counts',
                'unique_fixtures': len({(c['input']['event']['season'], c['input']['event']['official_game_id']) for c in captures.values()}),
                'by_product': by_product,
                'unpriced': sum(not c['roi_eligible'] for c in captures.values()),
                'unpriced_three_way_accuracy': unpriced_accuracy,
                'promoted': False, 'production_policy_changed': False, 'paper_wallet_changed': False}
