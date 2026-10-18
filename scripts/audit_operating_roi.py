"""Local exact-revision diagnostic. Not a recommendation or bankroll backtest."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prediction_ledger import PredictionLedger
from probability_validation_audit import audit, instant, probability


def evaluate(records):
    # Reuse the production verifier against an in-memory, read-only source.
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    verifier._read_verified_storage()
    grouped, settlements = defaultdict(list), defaultdict(list)
    for row in records:
        if row['record_type'] == 'prediction':
            grouped[row['event_id']].append(row)
        else:
            settlements[row['snapshot_id']].append(row)
    usable = []
    for predictions in grouped.values():
        labels = [s for p in predictions for s in settlements[p['snapshot_id']]]
        report = audit(predictions + labels, require_candidate=False)
        if not report['operating_descriptive']['exact_settled_operating_predictions']:
            continue
        # The audit selects the latest valid pre-freeze revision; find that same
        # revision without consulting which candidate has the better outcome.
        valid = [p for p in predictions if audit([p], require_candidate=False)['selected_pre_t30_events']]
        row = max(valid, key=lambda p: (instant(p['as_of']), instant(p['captured_at']), p['ledger_sequence']))
        pred = row['predictions']
        odds = pred.get('odds')
        if not isinstance(odds, (int, float)) or isinstance(odds, bool) or not 1 < odds < float('inf'):
            continue
        labels = settlements[row['snapshot_id']]
        usable.append(dict(event_id=row['event_id'], snapshot_id=row['snapshot_id'],
                           kickoff=row['kickoff'], known=max((s['captured_at'] for s in labels), key=instant),
                           captured=row['captured_at'],
                           probability=pred.get('probability'), odds=odds,
                           market=pred.get('market'), action=pred.get('action'),
                           hit=int(labels[0]['outcome']['result'] == 'hit')))
    def metrics(rows):
        if not rows:
            return {'n': 0, 'unit_stake_roi': None}
        return {'n': len(rows), 'hits': sum(r['hit'] for r in rows),
                'hit_rate': sum(r['hit'] for r in rows)/len(rows),
                'unit_stake_roi': sum(r['hit']*r['odds']-1 for r in rows)/len(rows)}
    by_market = defaultdict(list)
    for row in usable:
        by_market[row['market']].append(row)
    return {'hash_chain_verified': True, 'audit': audit(records, require_candidate=False),
            'descriptive_all_selected_offers': metrics(usable),
            'descriptive_p55_o150_proxy': metrics([r for r in usable if probability(r['probability'])
                                                   and r['probability'] >= .55 and r['odds'] >= 1.5]),
            'by_market': {k: metrics(v) for k, v in by_market.items()},
            'exploratory_temporal_comparison': compare(usable),
            'monthly_loss_limit': 50000, 'bankroll_replay_status': 'not_run_missing_pregame_ticket_contract',
            'promotion_allowed': False,
            'limitations': ['Descriptive selected-offer unit returns, not actual betting profit or two-leg ROI.',
                           'Unsettled events excluded: outcome coverage may be biased.',
                           'Threshold proxy does not reproduce historical recommendation eligibility.',
                           'Hash integrity is not independent authentication of source results.',
                           'No loss-cap simulation may silently replace missing pregame round/fixture identity.']}


def compare(rows):
    """Fixed market calibration comparison, not a search over profitable cutoffs.

    The data have already been inspected; this is exploratory, not an untouched
    confirmatory holdout. No training label observed after the split is used.
    """
    from scipy.optimize import minimize_scalar
    from scipy.special import expit, logit
    boundary = instant('2026-10-10T00:00:00Z')
    start = instant('2026-10-12T00:00:00Z')
    rows = [r for r in rows if probability(r['probability'])]
    train = [r for r in rows if instant(r['captured']) < boundary and instant(r['known']) < boundary]
    test = [r for r in rows if instant(r['captured']) >= start]
    if len(train) < 100 or len(test) < 30:
        return {'status': 'insufficient_samples', 'train': len(train), 'test': len(test)}
    def fit(sample):
        def objective(b):
            losses = []
            for r in sample:
                p = float(expit(logit(r['probability']) + b))
                losses.append(-r['hit']*math.log(p)-(1-r['hit'])*math.log1p(-p))
            return sum(losses)/len(losses) + .01*b*b
        result = minimize_scalar(objective, bounds=(-1,1), method='bounded')
        if not result.success:
            raise ValueError('Calibration failed')
        return min((0., float(result.x)), key=objective)
    pooled = fit(train)
    offsets = {m: fit([r for r in train if r['market'] == m])
               for m in {r['market'] for r in train}
               if sum(r['market'] == m for r in train) >= 100}
    output = {}
    for name in ('unchanged', 'pooled_intercept', 'market_intercept'):
        probabilities = [float(expit(logit(r['probability']) +
            (0 if name == 'unchanged' else pooled if name == 'pooled_intercept' else offsets.get(r['market'],pooled))))
            for r in test]
        picks = [r for r,p in zip(test,probabilities) if p >= .55 and r['odds'] >= 1.5]
        output[name] = {'test_n': len(test),
            'brier': sum((p-r['hit'])**2 for r,p in zip(test,probabilities))/len(test),
            'log_loss': sum(-r['hit']*math.log(p)-(1-r['hit'])*math.log1p(-p)
                            for r,p in zip(test,probabilities))/len(test),
            'selected_n': len(picks),
            'selected_unit_roi': sum(r['hit']*r['odds']-1 for r in picks)/len(picks) if picks else None}
    return {'status': 'exploratory_not_promotion_evidence', 'train_n': len(train),
            'train_labels_known_before': boundary.isoformat(), 'test_capture_from': start.isoformat(),
            'pooled_offset': pooled, 'market_offsets': offsets, 'models': output,
            'bankroll_replay': False, 'monthly_loss_limit_unchanged': 50000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    args = parser.parse_args()
    opener = gzip.open if args.ledger.suffix == '.gz' else open
    with opener(args.ledger, 'rb') as handle:
        raw = handle.read()
    result = evaluate([json.loads(line) for line in raw.splitlines() if line.strip()])
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
