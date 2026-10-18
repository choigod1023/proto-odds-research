"""Offline readiness audit, not a model promotion or betting policy."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import math


def instant(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def probability(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 < value < 1)


def audit(records, *, require_candidate=True):
    counts = Counter()
    rejected = Counter()
    versions = Counter()
    latest = {}
    settlements = {}
    for row in records:
        kind = row.get('record_type', 'unknown')
        counts[kind] += 1
        if kind == 'settlement':
            settlements.setdefault(row.get('snapshot_id'), []).append(row)
        elif kind == 'prediction':
            kickoff = instant(row.get('kickoff'))
            clocks = [instant(row.get(k)) for k in ('as_of', 'captured_at', 'market_observed_at')]
            if not kickoff or not all(clocks):
                rejected['missing_or_naive_time'] += 1
                continue
            if clocks[2] > clocks[0] or clocks[0] > clocks[1]:
                rejected['inconsistent_observation_order'] += 1
                continue
            if max(clocks) >= kickoff - timedelta(minutes=30):
                rejected['not_before_t30'] += 1
                continue
            if not row.get('event_id') or not row.get('snapshot_id'):
                rejected['missing_identity'] += 1
                continue
            # Choose the last pre-T30 revision BEFORE inspecting probabilities
            # or results. Never fall back to an older, conveniently settled pick.
            key = (clocks[0], clocks[1], int(row.get('ledger_sequence') or 0))
            event = row['event_id']
            if event not in latest or key > latest[event][0]:
                latest[event] = key, row
    usable = []
    for _, row in latest.values():
        pred = row.get('predictions') or {}
        detail = pred.get('probability_detail') or {}
        if not pred.get('selection_id') or not pred.get('offer_id'):
            rejected['selected_missing_selection_or_offer'] += 1
            continue
        if not probability(detail.get('market')) or (require_candidate and not probability(detail.get('ai_candidate'))):
            rejected['selected_missing_probability_pair'] += 1
            continue
        versions[str((row.get('model') or {}).get('residual_version') or 'unknown')] += 1
        rows = settlements.get(row['snapshot_id'], [])
        if not rows:
            rejected['selected_missing_exact_settlement'] += 1
            continue
        if any((r.get('outcome') or {}).get('selection_id') != pred['selection_id'] for r in rows):
            rejected['settlement_selection_mismatch'] += 1
            continue
        # Conservative: unresolved corrections/conflicts require a separate
        # audit, rather than selecting the most favorable settlement.
        outcomes = {(r.get('outcome') or {}).get('result') for r in rows}
        if len(outcomes) != 1:
            rejected['conflicting_settlements'] += 1
            continue
        if not outcomes <= {'hit', 'miss'}:
            rejected['void_or_nonbinary_result'] += 1
            continue
        kickoff = instant(row['kickoff'])
        times = [instant(r.get('settled_at')) for r in rows]
        captured = [instant(r.get('captured_at')) for r in rows]
        if not all(times) or not all(captured) or any(
                not r.get('source') or not str(r.get('settlement_version', '')).startswith('official-')
                or t < kickoff or c < t for r, t, c in zip(rows, times, captured)):
            rejected['unproven_settlement_provenance_or_time'] += 1
            continue
        usable.append((row, max(times + captured)))
    days = sorted({instant(r['kickoff']).date().isoformat() for r, _ in usable})
    return {
        'record_counts': dict(counts), 'selected_pre_t30_events': len(latest),
        'exclusions': dict(rejected), 'probability_pair_model_versions': dict(versions),
        'exact_settled_probability_pairs': sum(probability(
            (r['predictions'].get('probability_detail') or {}).get('ai_candidate')) for r, _ in usable),
        'distinct_utc_kickoff_days': len(days),
        'day_from': days[0] if days else None, 'day_to': days[-1] if days else None,
        'status': 'requires_temporal_split_and_provenance_review' if usable else 'blocked_no_labeled_pairs',
        'roi': None, 'promotion_allowed': False,
        'audit_mode': 'paired_model' if require_candidate else 'operating_lineage',
        'operating_descriptive': operating_metrics(usable) if not require_candidate else None,
        'limitations': ['Does not verify source authenticity or ledger hash chain.',
                       'event_id deduplication does not prove physical-event deduplication across reissues.',
                       'Selected-offer audit only; not a full alternative-market or combination backtest.',
                       'Training labels must be available before each subsequent split boundary.'],
    }


def operating_metrics(usable):
    """Unit-stake description, not recommendation-policy or bankroll replay."""
    priced = []
    coverage = Counter()
    for row, _ in usable:
        pred = row['predictions']
        odds = pred.get('odds')
        player = (row.get('features') or {}).get('research_player_inputs') or {}
        coverage[player.get('reason') or player.get('status') or 'not_recorded'] += 1
        if (isinstance(odds, (int, float)) and not isinstance(odds, bool)
                and math.isfinite(odds) and odds > 1):
            priced.append(row)
    return {'exact_settled_operating_predictions': len(usable),
            'with_valid_saved_odds': len(priced),
            'player_capture_reasons': dict(coverage),
            'note': 'Lineage coverage only; no bets or model promotion. AI absence does not exclude operating picks.'}
