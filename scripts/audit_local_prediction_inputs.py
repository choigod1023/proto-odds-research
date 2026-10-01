"""Read-only local ledger eligibility audit; does not infer results or ROI."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('naive timestamp')
    return parsed.astimezone(timezone.utc)


def audit(path):
    """Require both capture and observation before T-30, not observation alone."""
    digest = hashlib.sha256()
    types, models, excluded = Counter(), Counter(), Counter()
    events, snapshots, eligible_events = set(), set(), set()
    observed_times = []
    rows = eligible = result_fields = 0
    with Path(path).open('rb') as stream:
        for line_number, line in enumerate(stream, 1):
            digest.update(line)
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f'invalid JSON at line {line_number}') from exc
            if not isinstance(row, dict):
                raise ValueError(f'non-object at line {line_number}')
            rows += 1
            types[str(row.get('record_type'))] += 1
            models[str((row.get('model') or {}).get('operating_version'))] += 1
            event = row.get('event_id')
            snapshot = row.get('snapshot_id')
            if event:
                events.add(event)
            if snapshot:
                snapshots.add(snapshot)
            result_fields += any(row.get(k) is not None for k in
                                 ('result', 'settlement', 'outcome', 'settled_at'))
            try:
                observed = timestamp(row.get('market_observed_at'))
                observed_times.append(observed)
                captured = timestamp(row.get('captured_at'))
                kickoff = timestamp(row.get('kickoff'))
            except (ValueError, TypeError):
                excluded['invalid_or_missing_time'] += 1
                continue
            if row.get('record_type') != 'prediction' or not event or not snapshot:
                excluded['not_identified_prediction'] += 1
            elif observed > captured:
                excluded['observation_after_capture'] += 1
            elif (kickoff - max(observed, captured)).total_seconds() < 1800:
                excluded['not_captured_by_t30'] += 1
            else:
                eligible += 1
                eligible_events.add(event)
    return {
        'sha256': digest.hexdigest(), 'rows': rows,
        'record_types': dict(types), 'model_versions': dict(models),
        'unique_events': len(events), 'unique_snapshots': len(snapshots),
        'eligible_t30_rows': eligible, 'eligible_t30_events': len(eligible_events),
        'exclusions': dict(excluded), 'rows_with_result_fields': result_fields,
        'observed_min': min(observed_times).isoformat() if observed_times else None,
        'observed_max': max(observed_times).isoformat() if observed_times else None,
        'roi_evaluable': False,
        'limitation': ('Timing eligibility is not model/data provenance validation. '
                       'Repeated revisions are not independent matches. Official '
                       'settlement linkage and executable prices are not validated; '
                       'no ROI or production-policy replay is computed.'),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.ledger), ensure_ascii=False, indent=2))
