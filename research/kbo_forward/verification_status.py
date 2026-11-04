"""Read-only, fail-closed status for one prospective KBO verification.

The common vote/market comparison format is not yet established. Its mere
presence cannot prove price matching or settlement; integration requires an
explicit schema and source validation, not heuristic field matching.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from research.kbo_forward import official, ledger
from research.m1_service import observer


def _read(path, reader):
    if path is None or not Path(path).is_file():
        return [], 'missing'
    try:
        return reader(path), 'read'
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
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


def inspect(game_id, season, since, *, schedule_check=None, schedule_state=None,
            m1_ledger=None, legacy_ledger=None, common_ledger=None,
            archive_root=None, price_archive_root=None, parent_report=None):
    """Inspect sources without writing or treating a legacy baseline as M0 actual."""
    start = observer.moment(since)
    opportunity, opportunity_reason = _opportunity(schedule_check, schedule_state, game_id, season, since)
    m1, m1_status = _read(m1_ledger, observer.read)
    legacy, legacy_status = _read(legacy_ledger, ledger.read)
    common_rows, common_read = _read(common_ledger, observer.read)
    common_status = 'schema_unconfirmed' if common_read == 'read' else common_read
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
        'operating_same_price': {'complete': False, 'reason': 'common comparison schema and vote/market evidence unconfirmed'},
        'official_settled': {'complete': False, 'reason': 'matched common comparison settlement not verified'},
    }
    return {'game_id': game_id, 'season': season, 'since': start.isoformat(),
            'stages': stages, 'complete': all(s['complete'] for s in stages.values()),
            'sources': {'m1_ledger': m1_status, 'legacy_m0_ledger': legacy_status,
                        'common_comparison_ledger': common_status, 'parent_report': parent_status},
            'parent_report_summary': {'m1_status': parent.get('m1_status'),
                                      'm1_comparison': parent.get('m1_comparison')},
            'common_records_read': len(common_rows),
            'm1_candidate_captures': len(matches), 'verified_m0_actual_captures': len(accepted),
            'legacy_m0_captures_not_m0_actual': legacy_matches, 'capture_issues': issues,
            'scope': 'one prospective end-to-end game; not a 200-game performance certification'}
