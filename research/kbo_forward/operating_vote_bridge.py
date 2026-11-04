"""Offline, fail-closed bridge for prospective operating-price shadow samples.

Neither export metadata nor hashes authenticate an external clock. No writes,
network access, settlement, promotion, or operating ledger modification.
"""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.operating_ledger_inputs import latest_pre_cutoff
from research.m1_service.observer import canonical, digest, moment, _verify_market_bytes, _verify_archives
from research.m1_service.model import metadata, forecast, TEAM_NAMES

MAX_RECORDS = 1000
MAX_EXPORT_BYTES = 32 * 1024 * 1024


def load_operating_export(state, events, now):
    """Read a bounded JSON export; metadata never asserts source authenticity.

    state: {'export_path': path, 'price_archive_root': path,
            'm0_archive_root': path, 'm0_price_archive_root': path}.
    events: iterable of official game IDs (maximum 100).
    Export object: {'records': [raw ledger rows], 'evidence': {official_id:
    {'event':..., 'market':..., 'evidence':{'odds':...}}}, 'metadata':...}.
    Completeness checks the export's transaction claims and rows; it does not
    authenticate the SSH transport, server clock, or entire ledger chain.
    """
    wanted = list(events)
    if len(wanted) > 100 or any(not isinstance(x, str) or not x for x in wanted):
        raise ValueError('event_bound_or_identity')
    moment(now)
    path = Path(state['export_path'])
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError('export_byte_limit')
    raw = path.read_bytes()
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            content = stream.read(MAX_EXPORT_BYTES + 1)
        if len(content) > MAX_EXPORT_BYTES:
            raise ValueError('export_uncompressed_byte_limit')
    else:
        content = raw
    obj = json.loads(content)
    records = obj.get('records')
    evidence = obj.get('evidence', {})
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError('export_record_limit_or_shape')
    if not isinstance(evidence, dict) or len(evidence) > 100:
        raise ValueError('export_evidence_limit_or_shape')
    meta = obj.get('metadata', {})
    source_receipt = state.get('source_receipt')
    receipt_at = state.get('received_at') or (source_receipt or {}).get('received_at')
    complete, reasons = _export_completeness(records, meta, receipt_at, now)
    export_hash = hashlib.sha256(raw).hexdigest()
    if (not isinstance(source_receipt, dict) or source_receipt.get('transport') != 'flyctl_ssh'
            or source_receipt.get('export_sha256') != export_hash):
        complete = False
        reasons = ['operating_ssh_receipt_missing_or_mismatched']
    return {'records': records, 'evidence': {k: v for k, v in evidence.items() if k in wanted},
            'complete': complete, 'reasons': reasons,
            'export_sha256': export_hash, 'metadata': obj.get('metadata', {}),
            'source_receipt': source_receipt,
            'completeness_scope': 'internal_transaction_claim_consistency_not_external_authentication',
            'price_archive_root': state.get('price_archive_root'),
            'm0_archive_root': state.get('m0_archive_root'),
            'm0_price_archive_root': state.get('m0_price_archive_root')}


def _export_completeness(records, meta, received_at, now):
    try:
        exporter = Path(__file__).resolve().parents[2] / 'scripts/export_forward_operating_votes.py'
        if (meta['schema'] != 'operating-event-export-v1'
                or meta['query_mode'] != 'sqlite_readonly_transaction'
                or meta['exporter_sha256'] != hashlib.sha256(exporter.read_bytes().replace(b'\r\n', b'\n')).hexdigest()):
            raise ValueError('exporter_identity_unverified')
        queried = moment(meta['queried_at']); received = moment(received_at)
        if not queried <= received <= moment(now):
            raise ValueError('export_receipt_clock_invalid')
        counts = meta['events']
        if not isinstance(counts, dict) or not 0 < len(counts) <= 100:
            raise ValueError('export_event_counts_missing')
        if any(not isinstance(r, dict) or r.get('record_type') != 'prediction' for r in records):
            raise ValueError('invalid_export_prediction')
        if set(r['event_id'] for r in records) != set(counts):
            raise ValueError('export_event_set_mismatch')
        for event, claims in counts.items():
            rows = [r for r in records if r['event_id'] == event]
            sequences = [r['ledger_sequence'] for r in rows]
            if (not rows or len(rows) != claims['count'] or len(set(sequences)) != len(rows)
                    or max(sequences) != claims['max_sequence']):
                raise ValueError('export_count_or_sequence_mismatch')
            if any(r.get('record_hash') != _hash_row(r) for r in rows):
                raise ValueError('export_record_hash_mismatch')
            if any(moment(r['captured_at']) > queried for r in rows):
                raise ValueError('export_contains_future_capture')
            kickoffs = {moment(r['kickoff']) for r in rows}
            if len(kickoffs) != 1 or queried < next(iter(kickoffs)) - timedelta(minutes=30):
                raise ValueError('export_before_final_cutoff_or_kickoff_conflict')
        return True, ['full_chain_and_external_receipt_not_authenticated']
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
        return False, [str(exc) or 'export_completeness_unverified']


def _excluded(reason):
    return {'status': 'excluded', 'reason': reason, 'exclusion_reasons': [reason],
            'final_operating_performance_verified': False, 'hash_chain_verified': False}


def _hash_row(row):
    return digest(canonical({k: v for k, v in row.items() if k != 'record_hash'}))


def _offer_matches(row, option, market_id):
    """Reconstruct the actual offer-ID recipe using the exact candidate game number."""
    parts = market_id.split(':')
    if len(parts) != 3 or parts[0] != 'G101' or not parts[1].isdigit() or not parts[2].isdigit():
        return False
    if str(row['features']['fixture'].get('round')) != str(int(parts[1][-3:])):
        return False
    for number in (parts[2], int(parts[2])):
        fields = [option['selection_id'], row['features']['fixture']['round'], number]
        raw = '|'.join(json.dumps(v.strip() if isinstance(v, str) else v, ensure_ascii=False,
                                  sort_keys=True, separators=(',', ':')) for v in fields)
        if 'off_' + hashlib.sha256(raw.encode()).hexdigest()[:16] == option['offer_id']:
            return True
    return False


def _fixture_matches(fixture, event):
    if fixture.get('league') != 'KBO' or fixture.get('sport') != 'bs':
        return False
    if (fixture.get('home') != TEAM_NAMES.get(event.get('home_id'))
            or fixture.get('away') != TEAM_NAMES.get(event.get('away_id'))
            or fixture.get('year') != event.get('season')):
        return False
    match = re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})', str(fixture.get('date', '')))
    if not match:
        return False
    local = datetime(event['season'], *map(int, match.groups()), tzinfo=timezone(timedelta(hours=9)))
    return local == moment(event['kickoff_at'])


def match_operating(capture, export, now):
    """Compare the saved M0 choice at the later operating price; never reprice a trade.

    This is a first-common shadow sample. The caller must separately establish a
    complete final pre-T30 export and official result before performance claims.
    """
    try:
        return _match(capture, export, now)
    except (KeyError, TypeError, ValueError, OSError, OverflowError, AttributeError, IndexError) as exc:
        return _excluded(str(exc) or type(exc).__name__)


def _match(capture, export, now):
    now = moment(now)
    records = export['records']
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError('export_record_limit_or_shape')
    if capture.get('schema') != 'm1-pregame-rehearsal-v1' or capture.get('type') != 'capture':
        raise ValueError('not_m1_priced_capture')
    receipt = export.get('source_receipt')
    meta = export.get('metadata', {})
    if (not isinstance(receipt, dict) or receipt.get('transport') != 'flyctl_ssh'
            or receipt.get('export_sha256') != export.get('export_sha256')
            or not export.get('export_sha256')):
        raise ValueError('operating_ssh_receipt_missing_or_mismatched')
    if not moment(meta['queried_at']) <= moment(receipt['received_at']) <= now:
        raise ValueError('operating_ssh_receipt_clock_invalid')
    exporter = Path(__file__).resolve().parents[2] / 'scripts/export_forward_operating_votes.py'
    if (meta.get('schema') != 'operating-event-export-v1'
            or meta.get('query_mode') != 'sqlite_readonly_transaction'
            or meta.get('exporter_sha256') != hashlib.sha256(exporter.read_bytes().replace(b'\r\n', b'\n')).hexdigest()):
        raise ValueError('operating_export_provenance_missing')
    claims = meta['events']
    if not isinstance(claims, dict) or not 0 < len(claims) <= 100:
        raise ValueError('operating_export_events_invalid')
    if any(not isinstance(r, dict) or r.get('record_type') != 'prediction' for r in records):
        raise ValueError('operating_export_records_invalid')
    if set(r.get('event_id') for r in records) != set(claims):
        raise ValueError('operating_export_event_set_mismatch')
    for event_id, info in claims.items():
        group = [r for r in records if r['event_id'] == event_id]
        seq = [r['ledger_sequence'] for r in group]
        if (not group or len(group) != info['count'] or len(set(seq)) != len(seq)
                or max(seq) != info['max_sequence']):
            raise ValueError('operating_export_count_or_sequence_mismatch')
        if any(r.get('record_hash') != _hash_row(r) or moment(r['captured_at']) > moment(meta['queried_at']) for r in group):
            raise ValueError('operating_export_hash_or_clock_mismatch')
    payload = capture['input']; event = payload['event']; market = payload['market']
    if capture.get('hash') != digest(canonical({k: v for k, v in capture.items() if k != 'hash'})):
        raise ValueError('m0_record_hash_mismatch')
    if capture.get('input_sha256') != digest(canonical(payload)):
        raise ValueError('m0_input_hash_mismatch')
    if capture.get('model_metadata') != metadata():
        raise ValueError('m0_coefficient_metadata_mismatch')
    frozen = moment(capture['captured_at']); kickoff = moment(event['kickoff_at'])
    if frozen > now or frozen > kickoff - timedelta(minutes=30) or frozen < moment(metadata()['all_coefficients_preserved_by']):
        raise ValueError('m0_capture_time_invalid')
    _verify_archives(payload, export.get('m0_archive_root'), frozen)
    _verify_market_bytes(payload, export.get('m0_price_archive_root'))
    original_price_time = moment(payload['evidence']['odds']['received_at'])
    if original_price_time > frozen:
        raise ValueError('m0_price_after_capture')
    if capture['models'] != forecast(payload['row']):
        raise ValueError('m0_forecast_replay_mismatch')
    probs = capture['models']['m0_actual']['probabilities_2way']
    if set(probs) != {'home', 'away'} or any(type(v) not in (int, float) or not math.isfinite(v) for v in probs.values()):
        raise ValueError('invalid_m0_probabilities')
    if probs['home'] == probs['away']:
        raise ValueError('m0_selection_tied')
    m0_side = max(probs, key=probs.get)
    if capture['choices']['m0_actual'] != m0_side:
        raise ValueError('m0_choice_mismatch')
    if market['kind'] != 'full_game_2way' or market['settlement_rule'] != 'draw_void_refund':
        raise ValueError('unsupported_m0_product')
    op_rows = []
    for wrapper in records:
        row = wrapper.get('record_json', wrapper)
        if isinstance(row, str): row = json.loads(row)
        if not isinstance(row, dict): raise ValueError('invalid_operating_record')
        fixture = row.get('features', {}).get('fixture', {})
        ids = [fixture.get('source_event_id'), fixture.get('match_id')]
        direct = (event['official_game_id'] in ids
                  and all(not value or value == event['official_game_id'] for value in ids))
        fallback = not any(ids) and _fixture_matches(fixture, event)
        if (direct and _fixture_matches(fixture, event)) or fallback:
            op_rows.append(row)
    if not op_rows: raise ValueError('operating_official_game_id_not_found')
    selected = latest_pre_cutoff(op_rows)['selected']
    if len(selected) != 1: raise ValueError('operating_revision_missing_or_ambiguous')
    op = selected[0]
    if op['exclusion_reasons']:
        raise ValueError('operating_revision_excluded:' + ','.join(op['exclusion_reasons']))
    source = [r for r in op_rows if r.get('snapshot_id') == op['snapshot_id']]
    if len(source) != 1 or source[0].get('record_hash') != _hash_row(source[0]):
        raise ValueError('operating_record_hash_mismatch_or_duplicate')
    row = source[0]
    if row.get('schema_version') != 'pregame-prediction-ledger-v1' or not isinstance(row.get('ledger_sequence'), int):
        raise ValueError('operating_ledger_identity_invalid')
    parent = [r for r in records if isinstance(r, dict) and r.get('record_hash') == row.get('previous_record_hash')]
    parent_check = 'not_available_in_bounded_export'
    if parent:
        if (len(parent) != 1 or parent[0].get('record_hash') != _hash_row(parent[0])
                or parent[0].get('ledger_sequence') != row['ledger_sequence'] - 1):
            raise ValueError('operating_parent_mismatch')
        parent_check = 'adjacent_parent_hash_checked_not_full_chain'
    elif row['ledger_sequence'] == 1 and row.get('previous_record_hash') is None:
        parent_check = 'genesis_record_not_full_chain'
    if op['model_identity']['model_version'] != 'tipster-vote-v1' or op['tipster_hybrid'].get('selection_source') == 'market_fallback':
        raise ValueError('actual_operating_vote_required')
    if frozen > moment(op['as_of']): raise ValueError('m0_after_operating_as_of')
    if moment(op['captured_at']) > now: raise ValueError('operating_capture_in_future')
    if moment(op['kickoff']) != kickoff: raise ValueError('operating_kickoff_mismatch')
    roster = op['same_market_offers']
    sides = {'홈': 'home', '원정': 'away'}
    if op['operating']['market'] != '승패' or len(roster) != 2:
        raise ValueError('operating_market_mismatch')
    market_id = market['market_id']
    game_number_verified = all(_offer_matches(row, option, market_id) for option in roster)
    degraded = False
    if not game_number_verified:
        mapping = meta.get('mapping') or {}
        parts = market_id.split(':')
        if (mapping.get('mapping') != 'degraded_unique_fixture_round_two_way'
                or mapping.get('candidate_event_count') != 1
                or mapping.get('uniqueness_scope') != 'last_3_days_bounded_prediction_rows'
                or mapping.get('game_number_verified') is not False
                or mapping.get('internal_event_id') != row['event_id']
                or mapping.get('official_id') != event['official_game_id']
                or mapping.get('home_id') != event['home_id']
                or mapping.get('away_id') != event['away_id']
                or moment(mapping.get('kickoff')) != kickoff
                or mapping.get('market_id') != market_id
                or len(parts) != 3 or parts[0] != 'G101' or len(parts[1]) != 6
                or not all(p.isdigit() for p in parts[1:])
                or int(parts[1][:2]) != event['season'] % 100
                or str(row['features']['fixture'].get('round')) != str(int(parts[1][-3:]))):
            raise ValueError('operating_game_number_unverified')
        degraded = True
    prices = {sides[x['selection']]: x['odds'] for x in roster}
    if set(prices) != {'home', 'away'}:
        raise ValueError('operating_prices_incomplete')
    packet = export['evidence'].get(event['official_game_id'])
    verified_raw = False
    if packet is not None:
        op_market = packet['market']; op_event = packet['event']; receipt = packet['evidence']['odds']
        for key in ('official_game_id', 'season', 'home_id', 'away_id'):
            if op_event[key] != event[key]: raise ValueError('operating_price_fixture_mismatch')
        if moment(op_event['kickoff_at']) != kickoff: raise ValueError('operating_price_kickoff_mismatch')
        if (op_market['kind'] != 'full_game_2way' or op_market['settlement_rule'] != 'draw_void_refund'
                or op_market['market_id'] != market_id or op_market['odds'] != prices):
            raise ValueError('operating_product_or_prices_mismatch')
        if moment(receipt['received_at']) != moment(op['market_observed_at']):
            raise ValueError('operating_price_receipt_mismatch')
        _verify_market_bytes(packet, export.get('price_archive_root'))
        verified_raw = not degraded
    market_probs = {sides[x['selection']]: x.get('market_probability') for x in roster}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in market_probs.values()) or market_probs['home'] == market_probs['away']:
        raise ValueError('market_selection_missing_or_tied')
    exact = (verified_raw and market == op_market and original_price_time == moment(receipt['received_at'])
             and payload['evidence']['odds']['raw_sha256'] == receipt['raw_sha256'])
    return {'status': 'eligible', 'reason': None, 'exclusion_reasons': [],
            'comparison_kind': 'degraded_first_shadow' if degraded else ('matched_exact_original_frame' if exact else 'operating_price_shadow'),
            'original_m0_price_differs': market['odds'] != prices or original_price_time != moment(op['market_observed_at']),
            'original_m0_market': market, 'original_m0_price_received_at': original_price_time.isoformat(),
            'operating_record_hash': row['record_hash'], 'operating_snapshot_id': row['snapshot_id'],
            'official_game_id': event['official_game_id'], 'market_id': None if degraded else market_id,
            'candidate_m0_market_id': market_id, 'game_number_verified': game_number_verified,
            'operating_market_identity_verified': game_number_verified,
            'operating_settlement_rule_verified': False,
            'identity_limitation': 'offer_id_recipe_or_game_number_unverified' if degraded else None,
            'price_received_at': op['market_observed_at'], 'shared_prices': prices,
            'three_selections': {'m0_actual': m0_side, 'operating_vote': sides[op['operating']['selection']],
                                 'market': max(market_probs, key=market_probs.get)},
            'operating_as_of': op['as_of'], 'm0_captured_at': frozen.isoformat(),
            'parent_check': parent_check,
            'hash_chain_verified': False, 'final_operating_performance_verified': False,
            'official_raw_price_verified': verified_raw, 'formal_promotion_eligible': False,
            'source_stage': 'saved_operating_snapshot_transport_not_independently_authenticated',
            'sample_scope': 'degraded_first_shadow_not_final_operating_performance' if degraded else 'first_common_shadow_not_final_operating_performance',
            'source_scope': 'local archived bytes and clocks checked; no external timestamp authentication',
            'actual_execution_verified': False}
