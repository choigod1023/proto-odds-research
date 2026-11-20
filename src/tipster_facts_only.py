"""Offline facts gate and reconstructed-context control; no tipster pick inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pregame_evidence_gate import check_evidence

# These are claim types, never opinion, selection, handicap or total recommendations.
KINDS = {'starter', 'injury', 'recent_form', 'pitcher_form', 'rest', 'bullpen_workload'}
DUPLICATES = {'recent_form': ['form_diff', 'margin_diff', 'trend_diff', 'streak_diff'],
              'rest': ['rest_diff', 'b2b_home', 'b2b_away']}
CONTEXT = ['form_diff', 'margin_diff', 'trend_diff', 'streak_diff',
           'rest_diff', 'b2b_home', 'b2b_away']


def assess(claim, event_id, prediction_at, kickoff):
    """Fail closed. Caller supplies independently matched event and cutoff.

    Verification is a human/adapter assertion requiring its own pregame record;
    this gate does not turn a supplied URL into proof that the claim is true.
    Free text, including any final-pick field, is never emitted as model input.
    """
    kind = claim.get('kind')
    if kind not in KINDS:
        return {'status': 'not_a_fact_type'}
    status = check_evidence(claim, event_id, prediction_at, kickoff)
    if status != 'eligible':
        return {'status': status}
    verification = claim.get('verification', {})
    if verification.get('confirmed') is not True or not verification.get('source_url'):
        return {'status': 'not_independently_verified'}
    if not claim.get('source_url') or verification['source_url'] == claim['source_url']:
        return {'status': 'not_independently_verified'}
    status = check_evidence(verification, event_id, prediction_at, kickoff)
    if status != 'eligible':
        return {'status': 'verification_' + status}
    # Only explicitly typed values may enter a future encoder.
    value = claim.get('value')
    if type(value) not in (int, float, bool):
        return {'status': 'unencoded_fact'}
    import math
    if not math.isfinite(value):
        return {'status': 'invalid_value'}
    if claim.get('side') not in ('home', 'away'):
        return {'status': 'invalid_side'}
    return {'status': 'eligible', 'kind': kind, 'side': claim['side'], 'value': value,
            'existing_feature_overlap': DUPLICATES.get(kind, [])}


def run(games, claims_path):
    import numpy as np
    import pandas as pd
    from sport_hybrid_validation import load_data, fit, predict, brier
    from model_v2 import SPORT_FEATURES
    from accuracy_formula_lab import date_block_interval

    claims = json.loads(claims_path.read_text(encoding='utf8'))
    audit = [{'id': c['id'], 'kind': c['kind'],
              'overlap': DUPLICATES.get(c['kind'], []),
              **assess(c, c['event_id'], c.get('prediction_at'), c.get('kickoff'))}
             for c in claims]
    d, quality = load_data(games)
    result = {'games_sha256': hashlib.sha256(games.read_bytes()).hexdigest(),
              'claims_sha256': hashlib.sha256(claims_path.read_bytes()).hexdigest(),
              'quality': quality, 'claims': audit,
              'eligible_claims': sum(c['status'] == 'eligible' for c in audit),
              'tipster_incremental_roi': None,
              'scope': 'reconstructed context control, NOT text-source efficacy or production replay',
              'sports': {}}
    for sport, rows in d.groupby('sport'):
        cols = list(dict.fromkeys(['elo_diff', *SPORT_FEATURES[sport]]))
        extended = list(dict.fromkeys(cols + CONTEXT))
        folds = []
        for year in (2025, 2026):
            train = rows[rows.year < year]
            test = rows[rows.year == year].reset_index(drop=True)
            if len(train) < 100 or len(test) < 50:
                continue
            assert train.date.max() < test.date.min()
            baseline = predict(fit(train, cols, True), test)
            context = predict(fit(train, extended, True), test)
            y = test.y.to_numpy(int)
            odds = np.array(test.odds.tolist())
            scores, pnl = {}, {}
            for name, p in [('baseline', baseline), ('context', context)]:
                pick = p.argmax(1)
                pnl[name] = odds[np.arange(len(test)), pick] * (pick == y) - 1
                scores[name] = {'accuracy': float((pick == y).mean()),
                                'unit_roi': float(pnl[name].mean()),
                                'brier': float(brier(p, y).mean())}
            delta = pd.DataFrame({'date': test.date,
                'accuracy': (context.argmax(1) == y).astype(float) - (baseline.argmax(1) == y),
                'roi': pnl['context'] - pnl['baseline']})
            ci = date_block_interval(delta, ['accuracy', 'roi'], repeats=1000,
                                     seed=20261024, block_dates=7)
            folds.append({'year': year, 'train_n': len(train), 'test_n': len(test),
                          'start': str(test.date.min()), 'end': str(test.date.max()),
                          'scores': scores, 'paired_delta_ci': ci})
        result['sports'][sport] = {'baseline_columns': cols,
                                  'added_existing_columns': [c for c in extended if c not in cols],
                                  'folds': folds}
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--games', type=Path, required=True)
    ap.add_argument('--claims', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = run(args.games, args.claims)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'eligible_claims': result['eligible_claims'], 'sports': result['sports']}))
