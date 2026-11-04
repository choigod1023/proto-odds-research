"""Read-only audit of preserved player captures, not a backfilled prediction run.

The input ledger is operator-selected. No source flag, hash, or local timestamp
is treated as independent historical source authentication.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def instant(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.astimezone(timezone.utc) if d.utcoffset() is not None else None
    except ValueError:
        return None


def verify_chain(records):
    previous = None
    for n, row in enumerate(records, 1):
        unsigned = {k: v for k, v in row.items() if k != 'record_hash'}
        if (row.get('ledger_sequence') != n
                or row.get('previous_record_hash') != previous
                or row.get('record_hash') != digest(canonical(unsigned))):
            raise ValueError(f'invalid ledger sequence or hash at {n}')
        previous = row['record_hash']
    return previous


def describe(row):
    features = row.get('features') or {}
    capture = features.get('research_player_inputs') or {}
    ko, decision, observed = map(instant, (row.get('kickoff'), row.get('as_of'),
                                          capture.get('observed_at')))
    eligible = bool(ko and decision and observed and observed <= decision
                    and decision < ko - timedelta(minutes=30))
    lineup = capture.get('lineups') or {}
    sides = [lineup.get(s) or [] for s in ('home', 'away')]
    named = all(sides) and all(all(p.get('name') for p in side) for side in sides)
    league = (features.get('fixture') or {}).get('league', 'unknown')
    expected = 9 if league in ('KBO', 'MLB', 'NPB') else 11 if league in ('EPL', 'K리그1', 'K리그2', 'K1', 'K2') else None
    ids = all(sides) and all(all(p.get('player_id') or p.get('id')
                                for p in side) for side in sides)
    inputs = features.get('score_inputs') or {}
    status = capture.get('lineup_status') or {}
    learned = ((features.get('model_comparison') or {}).get('learned_candidates') or {})
    return {
        'event_id': row.get('event_id'), 'decision_id': row.get('decision_id'),
        'record_hash': row.get('record_hash'),
        'league': league,
        'source_game_id': capture.get('source_game_id'),
        'kickoff': row.get('kickoff'), 'as_of': row.get('as_of'),
        'observed_at': capture.get('observed_at'),
        'capture_status': capture.get('status'), 'capture_reason': capture.get('reason'),
        'before_t30_in_enclosing_ledger': eligible,
        'capture_and_decision_before_t30': bool(ko and decision and observed
                                                and max(decision, observed) < ko - timedelta(minutes=30)),
        'capture_after_decision_milliseconds': ((observed-decision).total_seconds()*1000
                                                if observed and decision and observed > decision else 0),
        'both_lineups_present_claim': (capture.get('coverage') or {}).get('both_lineups_present') is True,
        'both_lineups_named': bool(named), 'both_lineups_have_ids': bool(ids),
        'lineup_sizes': {'home': len(sides[0]), 'away': len(sides[1])},
        'expected_lineup_size': expected,
        'both_lineups_expected_count': expected is not None and all(len(s) == expected for s in sides),
        'lineup_state_claim': status.get('state'),
        'official_today_claim': status.get('official_today') is True,
        'training_eligible_claim': capture.get('training_eligible') is True,
        'score_inputs_both_present': inputs.get('home') is not None and inputs.get('away') is not None,
        'score_forecast_present': features.get('score_forecast') is not None,
        'candidate_input_present': isinstance(capture.get('candidate_input'), dict),
        'available_learned_candidate_names': sorted(k for k, v in learned.items()
                                                    if isinstance(v, dict) and v.get('status') == 'available'),
        'source_url': capture.get('source_url'),
        'limits': ['Ledger clocks and source status are preserved assertions, not independently authenticated receipts.',
                   'Named players or presence flags alone do not establish a usable frozen player model input.',
                   'A team residual candidate is not an M0/M1/M2 player forecast.'],
    }


def audit(records):
    terminal = verify_chain(records)
    types = Counter(r.get('record_type') for r in records)
    rows = [describe(r) for r in records if r.get('record_type') == 'prediction'
            and (r.get('features', {}).get('research_player_inputs') or {}).get('status') == 'captured']
    counters = defaultdict(Counter)
    latest_full = {}
    any_exact_order = set()
    flags = ['before_t30_in_enclosing_ledger', 'both_lineups_present_claim',
             'both_lineups_named', 'both_lineups_have_ids', 'official_today_claim',
             'score_inputs_both_present', 'score_forecast_present', 'candidate_input_present']
    for row in rows:
        c = counters[row['league']]
        c['captured_revisions'] += 1
        for flag in flags:
            c[flag] += bool(row[flag])
        if row['capture_and_decision_before_t30'] and row['both_lineups_named']:
            # Deduplicate known official identities across reissued internal IDs.
            identity = (row['league'], row['source_game_id'] or row['event_id'])
            if row['before_t30_in_enclosing_ledger']:
                any_exact_order.add(identity)
            prior = latest_full.get(identity)
            if prior is None or instant(row['as_of']) > instant(prior['as_of']):
                latest_full[identity] = row
    return {'record_types': dict(types), 'ledger_terminal_hash': terminal,
            'captured_revisions': len(rows),
            'captured_as_of_range': [min((r['as_of'] for r in rows), default=None),
                                     max((r['as_of'] for r in rows), default=None)],
            'league_revision_counts': dict(counters),
            'unique_named_lineup_cases_before_t30': len(latest_full),
            'named_lineup_cases_with_exact_clock_order': sum(r['before_t30_in_enclosing_ledger'] for r in latest_full.values()),
            'named_lineup_cases_with_any_exact_clock_order_revision': len(any_exact_order),
            'named_lineup_cases': sorted(latest_full.values(), key=lambda r: r['as_of']),
            'historical_2025_receipt_reconstruction': False,
            'audit_is_model_performance_evaluation': False,
            'external_authentication_proven': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--forward-state', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.ledger.read_bytes()
    decoded = gzip.decompress(raw) if args.ledger.suffix == '.gz' else raw
    records = [json.loads(line) for line in decoded.splitlines() if line.strip()]
    result = audit(records)
    result['input_ledger'] = {'sha256': digest(raw), 'path': str(args.ledger), 'bytes': len(raw)}
    result['forward_local_state'] = {}
    if args.forward_state:
        for name in ('history-preflight.json', 'summary.json', 'last-run.json'):
            path = args.forward_state / name
            if path.is_file():
                body = path.read_bytes()
                result['forward_local_state'][name] = {'sha256': digest(body), 'saved_value': json.loads(body)}
        result['forward_local_state_caveat'] = 'Read-only saved local state; not a fresh execution or scheduler liveness check.'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    print(json.dumps({'record_types': result['record_types'], 'named_lineup_cases': result['unique_named_lineup_cases_before_t30']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
