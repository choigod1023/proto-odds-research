"""Read-only, fail-closed status for one prospective KBO verification.

A first-common shadow is rechecked from archived evidence. It is not final
operating performance, externally authenticated execution, or promotion.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from research.kbo_forward import official, ledger
from research.m1_service import observer


def _read(path, reader):
    if path is None or not Path(path).is_file():
        return [], 'missing'
    try:
        return reader(path), 'read'
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        return [], 'invalid: ' + str(exc)[:200]


def _opportunity(path, state, game_id, season, since):
    if path is None or state is None:
        return False, 'schedule evidence missing'
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        client = official.OfficialClient.__new__(official.OfficialClient)
        client.root = Path(state)
        for item in data['future_scheduled']:
            g, rec = item['game'], item['receipt']
            raw = client.verified_body(rec, official.SCHEDULE)
            body = json.loads(raw)
            found = [x for x in body['game'] if x.get('G_ID') == game_id]
            kickoff = datetime.strptime(g['G_DT']+' '+g['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=official.KST)
            received = observer.moment(rec['finished_at'])
            if (len(found) == 1 and found[0] == g and str(g['SEASON_ID']) == str(season)
                and str(g['LE_ID']) == '1' and str(g['GAME_STATE_SC']) == '1'
                and str(g['CANCEL_SC_ID']) == '0' and rec['params']['date'] == g['G_DT']
                and observer.moment(since) <= received <= kickoff.astimezone(timezone.utc)-timedelta(minutes=30)):
                return True, 'verified official schedule receipt'
        return False, 'matching prospective schedule receipt missing'
    except (OSError, ValueError, TypeError, KeyError, official.SourceError) as exc:
        return False, 'invalid schedule evidence: '+str(exc)[:150]


ARMS = ('m0_actual', 'operating_vote', 'market')
SCHEMA = 'prospective-common-shadow-v1'


def _state_file(root, name):
    if not name:
        raise ValueError('archive path missing')
    root = Path(root).resolve()
    path = Path(name)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('archive path outside state or missing')
    return path


def _common_capture(row, m0, root, archive_root, price_archive_root, now):
    from research.kbo_forward import operating_vote_bridge as bridge
    if row.get('schema') != SCHEMA or row.get('type') != 'paired_capture':
        raise ValueError('unsupported common capture schema')
    event = m0['input']['event']; market = m0['input']['market']
    stamp = observer.moment(row['captured_at']); kickoff = observer.moment(event['kickoff_at'])
    if not m0['roi_eligible'] or row['m0_capture_hash'] != m0['hash'] or row['event'] != event:
        raise ValueError('common M0 or event identity differs')
    if not observer.moment(m0['captured_at']) <= stamp <= min(now, kickoff-timedelta(minutes=30)):
        raise ValueError('common capture time invalid')
    key = observer.digest(observer.canonical({'official_game_id': event['official_game_id'], 'season': event['season']}))
    if row['comparison_key'] != key:
        raise ValueError('comparison key differs')
    cm = row['market']; odds = cm['odds']; picks = row['selections']
    if (cm['kind'] != 'full_game_2way' or cm['settlement_rule'] != 'draw_void_refund'
            or cm['market_id'] != market['market_id'] or market['kind'] != cm['kind']
            or market['settlement_rule'] != cm['settlement_rule'] or set(odds) != {'home', 'away'}
            or set(picks) != set(ARMS) or any(x not in ('home', 'away') for x in picks.values())):
        raise ValueError('common product, prices or selections differ')
    for value in odds.values():
        n = Decimal(str(value))
        if isinstance(value, bool) or not n.is_finite() or n <= 1:
            raise ValueError('invalid shared odds')
    export_path = _state_file(root, row.get('export_path'))
    receipt_path = _state_file(root, row.get('source_receipt_path'))
    if export_path.stat().st_size > 32*1024*1024 or receipt_path.stat().st_size > 1024*1024:
        raise ValueError('export or receipt too large')
    receipt = json.loads(receipt_path.read_bytes())
    export_hash = hashlib.sha256(export_path.read_bytes()).hexdigest()
    if (not re.fullmatch(r'[0-9a-f]{64}', str(row.get('export_sha256', '')))
            or export_hash != row['export_sha256'] or receipt != row.get('source_receipt')
            or receipt.get('transport') != 'flyctl_ssh' or receipt.get('export_sha256') != export_hash
            or observer.moment(receipt['received_at']) > stamp):
        raise ValueError('export bytes or receipt differ')
    config = {'export_path': export_path, 'source_receipt': receipt,
              'price_archive_root': Path(root)/'operating-price-evidence',
              'm0_archive_root': archive_root, 'm0_price_archive_root': price_archive_root}
    export = bridge.load_operating_export(config, [event['official_game_id']], stamp.isoformat())
    if export['export_sha256'] != export_hash:
        raise ValueError('loaded export hash differs')
    actual = bridge.match_operating(m0, export, stamp.isoformat())
    if (actual.get('status') != 'eligible' or actual.get('official_game_id') != event['official_game_id']
            or actual.get('market_id') != cm['market_id'] or actual.get('operating_record_hash') != row['operating_record_hash']
            or actual.get('shared_prices') != odds or actual.get('three_selections') != picks
            or not observer.moment(m0['captured_at']) <= observer.moment(actual['operating_as_of']) <= stamp):
        raise ValueError('replayed bridge differs from common capture')
    return {'official_raw_operating_price_verified': actual.get('official_raw_price_verified') is True,
            'comparison_kind': actual.get('comparison_kind')}


def _common_settlement(row, cap, m1, now):
    if row.get('schema') != SCHEMA or row.get('type') != 'paired_settlement':
        raise ValueError('unsupported common settlement schema')
    if row['capture_hash'] != cap['hash'] or row['comparison_key'] != cap['comparison_key']:
        raise ValueError('settlement capture linkage differs')
    parents = [r for r in m1 if r.get('type') == 'settlement' and r.get('hash') == row['parent_m0_settlement_hash']]
    if len(parents) != 1 or parents[0].get('capture_hash') != cap['m0_capture_hash']:
        raise ValueError('parent M0 settlement missing')
    parent = parents[0]; ev = row['evidence']; event = cap['event']; econ = row['economics']
    if ev != parent['evidence'] or observer.digest(observer.canonical(ev['body'])) != ev['sha256']:
        raise ValueError('official result evidence differs')
    uri = urlparse(ev['uri'])
    if uri.scheme != 'https' or uri.hostname not in ('koreabaseball.com', 'www.koreabaseball.com') or uri.path != urlparse(official.SCHEDULE).path:
        raise ValueError('official schedule endpoint required')
    received = observer.moment(ev['received_at']); recorded = observer.moment(row['recorded_at'])
    if not observer.moment(cap['captured_at']) <= received <= recorded <= now:
        raise ValueError('result receipt time invalid')
    games = [g for g in ev['body']['game'] if g.get('G_ID') == event['official_game_id']]
    if len(games) != 1:
        raise ValueError('official result identity not unique')
    g = games[0]
    day = observer.moment(event['kickoff_at']).astimezone(official.KST).strftime('%Y%m%d')
    if (str(g['SEASON_ID']) != str(event['season']) or str(g['HOME_ID']) != str(event['home_id'])
            or str(g['AWAY_ID']) != str(event['away_id']) or g['G_DT'] != day):
        raise ValueError('official result fixture differs')
    status = econ['status']; parent_econ = parent['economics']
    if status != parent_econ['status']:
        raise ValueError('parent result status differs')
    if status == 'cancelled':
        if str(g['CANCEL_SC_ID']) == '0' or '취소' not in str(g.get('CANCEL_SC_NM', '')):
            raise ValueError('official cancellation missing')
        h = a = winner = None
    elif status == 'final':
        if (received < observer.moment(event['kickoff_at']) or str(g['GAME_STATE_SC']) != '3'
                or str(g['GAME_RESULT_CK']) != '1' or str(g['CANCEL_SC_ID']) != '0'):
            raise ValueError('official final or timing invalid')
        if not all(re.fullmatch(r'[0-9]+', str(g[k])) for k in ('B_SCORE_CN', 'T_SCORE_CN')):
            raise ValueError('official score invalid')
        h, a = int(g['B_SCORE_CN']), int(g['T_SCORE_CN'])
        if (h, a) != (parent_econ['home_score'], parent_econ['away_score']):
            raise ValueError('parent score differs')
        winner = 'home' if h > a else 'away' if a > h else 'draw'
    else:
        raise ValueError('unsupported result status')
    if (econ['winner'], econ['home_score'], econ['away_score']) != (winner, h, a) or set(econ['profits']) != set(ARMS):
        raise ValueError('settlement outcome differs')
    for arm in ARMS:
        pick = cap['selections'][arm]; refund = status == 'cancelled' or winner == 'draw'
        net = Decimal(0) if refund else Decimal(str(cap['market']['odds'][pick]))-1 if pick == winner else Decimal(-1)
        p = econ['profits'][arm]
        if (p['selection'] != pick or p['refund'] is not refund or p['hit'] != (None if refund else pick == winner)
                or not Decimal(str(p['net_units'])).is_finite() or Decimal(str(p['net_units'])) != net):
            raise ValueError('recomputed common profit differs')
    return True


def inspect(game_id, season, since, *, schedule_check=None, schedule_state=None,
            m1_ledger=None, legacy_ledger=None, common_ledger=None,
            archive_root=None, price_archive_root=None, parent_report=None):
    """Inspect sources without writing or treating a legacy baseline as M0 actual."""
    start = observer.moment(since)
    opportunity, opportunity_reason = _opportunity(schedule_check, schedule_state, game_id, season, since)
    m1, m1_status = _read(m1_ledger, observer.read)
    legacy, legacy_status = _read(legacy_ledger, ledger.read)
    common_rows, common_read = _read(common_ledger, observer.read)
    common_status = common_read
    parent = {}
    parent_status = 'missing'
    if parent_report is not None and Path(parent_report).is_file():
        try:
            parent = json.loads(Path(parent_report).read_text(encoding='utf-8'))
            parent_status = 'read'
        except (OSError, ValueError, TypeError) as exc:
            parent_status = 'invalid: '+str(exc)[:150]
    matches = [r for r in m1 if r.get('type') == 'capture'
               and (r.get('input') or {}).get('event', {}).get('official_game_id') == game_id
               and str((r.get('input') or {}).get('event', {}).get('season')) == str(season)]
    accepted = []
    issues = []
    for row in matches:
        try:
            payload = row['input']; event = payload['event']
            stamp = observer.moment(row['captured_at']); kickoff = observer.moment(event['kickoff_at'])
            if row.get('schema') != 'm1-pregame-rehearsal-v1' or stamp < start or stamp > kickoff-timedelta(minutes=30) or stamp > datetime.now(timezone.utc):
                raise ValueError('capture outside prospective window')
            if observer.digest(observer.canonical(payload)) != row['input_sha256']:
                raise ValueError('input hash mismatch')
            ledger.validate_model_snapshot(payload, stamp, unpriced=not row['roi_eligible'])
            observer._verify_archives(payload, archive_root, stamp)
            if row['roi_eligible']:
                observer._verify_market_bytes(payload, price_archive_root)
            forecasts = observer.forecast(payload['row'])
            if row['models'] != forecasts or row['model_metadata'] != observer.metadata():
                raise ValueError('model forecast or frozen metadata differs')
            if 'm0_actual' not in row['models']:
                raise ValueError('m0_actual missing')
            accepted.append(row)
        except (OSError, ValueError, TypeError, KeyError, official.SourceError) as exc:
            issues.append(str(exc)[:200])
    common_issues, verified_common, settled_common = [], [], []
    now = datetime.now(timezone.utc)
    candidates = [r for r in common_rows if r.get('type') == 'paired_capture'
                  and (r.get('event') or {}).get('official_game_id') == game_id
                  and str((r.get('event') or {}).get('season')) == str(season)]
    for row in candidates:
        try:
            parents = [r for r in accepted if r.get('hash') == row.get('m0_capture_hash')]
            if len(parents) != 1 or common_ledger is None:
                raise ValueError('verified unique M0 capture missing')
            metadata = _common_capture(row, parents[0], Path(common_ledger).resolve().parent, archive_root, price_archive_root, now)
            verified_common.append((row, metadata))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, InvalidOperation, official.SourceError) as exc:
            common_issues.append(str(exc)[:200])
    if len(candidates) > 1:
        common_issues.append('duplicate common captures for fixture')
        verified_common = []
    if verified_common:
        cap = verified_common[0][0]
        revisions = [r for r in common_rows if r.get('type') == 'paired_settlement' and r.get('comparison_key') == cap['comparison_key']]
        if revisions:
            try:
                previous = None
                for revision in revisions:
                    if revision.get('supersedes_hash') != (previous['hash'] if previous else None):
                        raise ValueError('settlement correction chain differs')
                    if previous and observer.moment(revision['evidence']['received_at']) <= observer.moment(previous['evidence']['received_at']):
                        raise ValueError('settlement correction time invalid')
                    _common_settlement(revision, cap, m1, now)
                    previous = revision
                parent_revisions = [r for r in m1 if r.get('type') == 'settlement' and r.get('capture_hash') == cap['m0_capture_hash']]
                if not parent_revisions or previous['parent_m0_settlement_hash'] != parent_revisions[-1]['hash']:
                    raise ValueError('common result is not latest parent settlement')
                settled_common.append(previous)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
                common_issues.append(str(exc)[:200])
    # A source-verified capture also proves an announced roster. A separate
    # announcement with no capture is intentionally not inferred from flags.
    captured = bool(accepted)
    legacy_matches = sum(1 for r in legacy if r.get('type') in ('capture', 'unpriced_forecast')
                         and (r.get('input') or {}).get('event', {}).get('official_game_id') == game_id
                         and str((r.get('input') or {}).get('event', {}).get('season')) == str(season))
    stages = {
        'opportunity': {'complete': opportunity, 'reason': opportunity_reason},
        'announced': {'complete': captured, 'reason': 'verified capture contains announcement' if captured else 'independent announcement not verified'},
        'captured': {'complete': captured, 'reason': 'verified prospective m1 ledger m0_actual' if captured else 'verified prospective m0_actual missing'},
        'operating_same_price': {'complete': bool(verified_common), 'reason': 'replayed first-common shadow' if verified_common else 'verified common shadow missing'},
        'official_settled': {'complete': bool(settled_common), 'reason': 'official evidence and economics verified' if settled_common else 'matched common settlement not verified'},
    }
    return {'game_id': game_id, 'season': season, 'since': start.isoformat(),
            'stages': stages, 'complete': all(s['complete'] for s in stages.values()),
            'sources': {'m1_ledger': m1_status, 'legacy_m0_ledger': legacy_status,
                        'common_comparison_ledger': common_status, 'parent_report': parent_status},
            'parent_report_summary': {'m1_status': parent.get('m1_status'),
                                      'm1_comparison': parent.get('m1_comparison')},
            'common_records_read': len(common_rows), 'common_issues': common_issues,
            'first_common_shadow': {'verified': bool(verified_common),
                                    'price_source': verified_common[0][1] if verified_common else None,
                                    'settled': bool(settled_common)},
            'final_operating_performance_verified': False, 'formal_promotion_eligible': False,
            'external_source_authentication': False,
            'm1_candidate_captures': len(matches), 'verified_m0_actual_captures': len(accepted),
            'legacy_m0_captures_not_m0_actual': legacy_matches, 'capture_issues': issues,
            'scope': 'one prospective end-to-end game; not a 200-game performance certification'}
