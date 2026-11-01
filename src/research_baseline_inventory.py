"""Outcome-blind inventory of saved operating baselines, never an ROI report.

Reuses the operating input contract. Multiple revisions are not multiple games;
legacy predictions are not silently relabeled as the current vote policy.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .operating_ledger_inputs import latest_pre_cutoff, VOTE_POLICY


def _aware(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def inventory(records):
    records = list(records)
    if any(not isinstance(row, Mapping) or 'record_json' in row for row in records):
        raise ValueError('expected decoded ledger objects, not SQL wrappers')
    selected = latest_pre_cutoff(records)
    by_league, policy_counts, excluded = {}, Counter(), Counter()
    eligible = current = player_captured = 0
    for row in selected['selected']:
        identity = row['model_identity']
        policy = identity.get('policy') or identity.get('model_version') or 'unknown'
        policy_counts[policy] += 1
        league = str(row['fixture'].get('league') or 'unknown')
        bucket = by_league.setdefault(league, Counter())
        bucket['selected_events'] += 1
        if row['roi_input_eligible']:
            eligible += 1
            bucket['input_eligible_events'] += 1
            if identity.get('policy') == VOTE_POLICY:
                current += 1
                bucket['current_policy_input_eligible_events'] += 1
        excluded.update(row['exclusion_reasons'])
        capture = row['source_features'].get('research_player_inputs')
        if isinstance(capture, Mapping) and capture.get('status') == 'captured':
            player_captured += 1
            bucket['player_capture_present_events'] += 1
    for row in selected['rejected']:
        excluded.update(row.get('exclusion_reasons', []))
    times = [t for r in records if r.get('record_type') == 'prediction'
             and (t := _aware(r.get('as_of'))) is not None]
    return {
        'schema_version': 'saved-baseline-inventory-v1',
        'records': len(records),
        'record_types': dict(Counter(str(r.get('record_type')) for r in records)),
        'raw_distinct_prediction_event_ids': len({r['event_id'] for r in records
            if r.get('record_type') == 'prediction' and isinstance(r.get('event_id'), str) and r['event_id']}),
        'selected_events': len(selected['selected']), 'rejected_revisions_or_events': len(selected['rejected']),
        'input_eligible_selected_events': eligible,
        'current_policy': VOTE_POLICY, 'current_policy_input_eligible_selected_events': current,
        'player_capture_present_selected_events': player_captured,
        'selected_policy_counts': dict(policy_counts),
        'by_league': {k: dict(v) for k, v in sorted(by_league.items())},
        'exclusion_reasons_overlapping': dict(sorted(excluded.items())),
        'prediction_as_of_range_utc': [min(times).isoformat(), max(times).isoformat()] if times else None,
        'selection_rule': selected['selection_rule'],
        'cutoff_minutes': selected['cutoff_minutes'],
        'result_used': False, 'hash_chain_verified': False,
        'external_receipt_verified': False, 'roi_evaluable': False,
        'limitation': ('Saved local snapshot only, not a live production export. Input eligibility is not '
            'official fixture/date/settlement, historical source availability or execution-price certification. '
            'Player capture presence is not confirmed-lineup or feature-time qualification. '
            'No model replay, selection, fitting or ROI calculation was performed.'),
    }


def audit(path):
    # Hash and parse the same bytes; do not attribute a later file read to this SHA.
    payload = Path(path).read_bytes()
    rows = [json.loads(line) for line in payload.decode('utf-8-sig').splitlines() if line.strip()]
    return {**inventory(rows), 'source_sha256': hashlib.sha256(payload).hexdigest(),
            'source_bytes': len(payload)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.ledger.resolve() == args.output.resolve():
        raise ValueError('output must not overwrite the ledger')
    result = audit(args.ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('records','selected_events',
        'input_eligible_selected_events','current_policy_input_eligible_selected_events','roi_evaluable')}))


if __name__ == '__main__':
    main()
