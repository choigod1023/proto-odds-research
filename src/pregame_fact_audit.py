"""Local-only evidence audit. Missing paired predictions produce null, not zero ROI."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

SPORTS = ('bs', 'sc', 'bk', 'vl')


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.utcoffset() is not None else None
    except ValueError:
        return None


def gate(row):
    if row.get('kind') not in ('starter', 'absence', 'rotation'):
        return 'unsupported_fact'
    if not row.get('event_id'):
        return 'missing_official_event_match'
    observed, cutoff, kickoff = (stamp(row.get(k)) for k in
                                ('observed_at', 'prediction_at', 'kickoff_at'))
    if not all((observed, cutoff, kickoff)):
        return 'missing_aware_cutoff'
    if observed > cutoff or cutoff > kickoff - timedelta(minutes=30):
        return 'not_available_at_frozen_prediction'
    verification = row.get('verification') or {}
    verified = stamp(verification.get('observed_at'))
    if (verification.get('confirmed') is not True or not verified or verified > cutoff
            or not verification.get('source_url') or not row.get('source_url')
            or verification['source_url'] == row['source_url']):
        return 'missing_independent_pregame_confirmation'
    return 'eligible_for_feature_encoding'


def audit(records, history_path=None):
    audited = [{'id': r.get('id'), 'sport': r.get('sport'), 'status': gate(r)}
               for r in records]
    result = {'scope': 'evidence availability; no invented model probabilities',
              'monthly_loss_limit_krw': 50000, 'stake_krw': 10000,
              'ticket_legs': 2, 'weekly_ticket_variants': [2, 3],
              'budget_scope': 'all sports combined, separately for each model arm',
              'simulation_status': 'not_run; budget is a protocol constraint, not a measured result',
              'claims': audited, 'sports': {}}
    for sport in SPORTS:
        rows = [r for r in audited if r['sport'] == sport]
        result['sports'][sport] = {
            'collected_claims': len(rows),
            'eligibility': dict(Counter(r['status'] for r in rows)),
            'paired_model_evaluation': 'not_run_missing_fitted_challenger_and_paired_pregame_records',
            'hit_rate_delta': None, 'roi_delta': None, 'monthly_profit_delta': None,
            'drawdown_delta': None, 'profitable_month_fraction_delta': None}
    if history_path:
        raw = history_path.read_bytes()
        rows = json.loads(raw)
        result['retrospective_starters'] = {
            'sha256': hashlib.sha256(raw).hexdigest(), 'records': len(rows),
            'both_starters_named': sum(bool(r.get('home_starter') and r.get('away_starter')) for r in rows),
            'aware_observed_at': sum(stamp(r.get('observed_at')) is not None for r in rows),
            'date_range': [min(r.get('date', '') for r in rows), max(r.get('date', '') for r in rows)],
            'limitation': 'Actual starters in retrospective results do not prove announced starters were known pregame.'}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claims', type=Path, required=True)
    parser.add_argument('--historical-starters', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.claims.read_text(encoding='utf-8'))
    if isinstance(records, dict):
        records = records['records']
    result = audit(records, args.historical_starters)
    result['claims_sha256'] = hashlib.sha256(args.claims.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'claims'}, ensure_ascii=False))
