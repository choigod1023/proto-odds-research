"""Read-only local availability audit; never infer a missing prediction."""
import argparse
import collections
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1')
VOTE = Path('C:/Users/user/Documents/Codex/vote-accuracy/experiments/vote-accuracy-same-period-20261104')


def read(path):
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def lines(data):
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def dt(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return value.astimezone(timezone.utc)


def before_cutoff(kickoff, *times):
    try:
        return bool(times) and all(dt(t) <= dt(kickoff) - timedelta(minutes=30) for t in times)
    except (ValueError, TypeError, AttributeError):
        return False


def describe(path, jsonl=False):
    data, sha = read(path)
    return (lines(data) if jsonl else json.loads(data)), {'sha256': sha, 'bytes': len(data)}


def audit(root=ROOT, state=STATE, vote=VOTE):
    evidence = {}
    def get(name, path, jsonl=False):
        obj, evidence[name] = describe(path, jsonl)
        return obj
    pregame = get('local_pregame', root / 'data/raw/prediction_ledger/pregame.jsonl', True)
    revisions = get('local_revisions', root / 'data/raw/recommendation_revisions.jsonl', True)
    forward = get('local_forward', state / 'paired-ledger.jsonl', True)
    last = get('local_forward_last_run', state / 'last-run.json')
    preflight = get('local_history_preflight', state / 'history-preflight.json')
    cohort = get('prior_vote_experiment_inputs', vote / 'inputs.json')
    manifest = get('prior_vote_experiment_manifest', vote / 'input-manifest.json')
    kbo = [r for r in cohort if r.get('sport') == 'bs' and r.get('fixture', {}).get('league') == 'KBO']
    opportunities = [r for r in forward if r.get('type') == 'opportunity']
    captures = [r for r in forward if r.get('type') in ('capture', 'unpriced_forecast')]
    matches = []
    for v in kbo:
        for o in opportunities:
            event = o.get('event', {})
            fixture = v.get('fixture', {})
            if (event.get('home_name'), event.get('away_name'), event.get('kickoff_at')) and (
                event.get('home_name') == fixture.get('home') and event.get('away_name') == fixture.get('away')
                and dt(event['kickoff_at']) == dt(v['kickoff'])):
                matches.append({'vote_snapshot': v['snapshot_id'], 'official_game_id': event.get('official_game_id'),
                    'kickoff': v['kickoff'], 'vote_captured_at': v.get('captured_at'),
                    'vote_times_before_cutoff': bool(v.get('evidence')) and before_cutoff(v['kickoff'], v.get('as_of'), v.get('captured_at'), v.get('market_observed_at'), *(e.get('observed_at') for e in v.get('evidence', []))),
                    'opportunity_received_at': o.get('observed_at'),
                    'opportunity_before_cutoff': before_cutoff(v['kickoff'], o.get('observed_at')),
                    'forward_forecast_records_for_event': sum(c.get('event_key') == o.get('event_key') for c in captures)})
    serial = lambda x: json.dumps(x, ensure_ascii=False)
    return {'read_at_utc': datetime.now(timezone.utc).isoformat(), 'evidence': evidence,
        'scope': 'Local file bytes and prior experiment summary only; no direct production response. No source-post-received or retrospective cohort is used as prospective sample.',
        'local_pregame': {'rows': len(pregame), 'captured_at_min': min((r.get('captured_at', '') for r in pregame), default=None), 'captured_at_max': max((r.get('captured_at', '') for r in pregame), default=None), 'tipster_vote_mentions': sum('tipster-vote-v1' in serial(r) for r in pregame)},
        'local_revisions': {'rows': len(revisions), 'changed_at_min': min((r.get('changed_at', '') for r in revisions), default=None), 'changed_at_max': max((r.get('changed_at', '') for r in revisions), default=None), 'tipster_vote_mentions': sum('tipster-vote-v1' in serial(r) for r in revisions)},
        'prior_vote_summary': {'rows': len(cohort), 'kbo_rows': len(kbo), 'raw_chain_claimed_by_prior_experiment': manifest.get('full_ledger_chain_verified'), 'raw_ledger_bytes_present_in_this_audit': False},
        'forward': {'record_types': dict(collections.Counter(r.get('type') for r in forward)), 'last_run_finished_at': last.get('finished_at'), 'last_run_status': last.get('status'), 'last_run_price_requests': last.get('price_request_count'), 'preflight_received_at': preflight.get('received_at'), 'preflight_predictions_generated': preflight.get('predictions_generated'), 'preflight_unresolved_history_identities': preflight.get('unresolved_history_identities')},
        'candidate_fixture_matches': matches,
        'strict_common_sample': {'count': 0 if not captures else None, 'status': 'unavailable' if not captures else 'not_evaluated', 'reason': 'No forward capture or unpriced forecast records in the inspected ledger.' if not captures else 'Manual lineage verification required; no automatic eligibility claim.'},
        'lineage': 'Frozen KBO 2023-25 M0 baseline must be bound to coefficient artifact and actual pre-cutoff input/forecast; e0:residual:0.03 and historical reconstructed forecasts are not interchangeable. Seed/history preparation is not a forecast or proof of earlier availability. Price receipt, matching product and independently validated bytes remain separate requirements.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'strict_common_sample': result['strict_common_sample'], 'candidate_fixture_matches': result['candidate_fixture_matches']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
