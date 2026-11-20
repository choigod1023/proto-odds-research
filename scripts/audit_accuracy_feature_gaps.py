"""Offline mechanism probes, not evidence of a model's predictive improvement."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.special import softmax

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_decision import build_decision_snapshot, validate_decision_snapshot


AT = '2026-10-25T22:00:00+00:00'


def selection_probe():
    """Vary recognized context while holding captured odds and vote fixed."""
    game = dict(league='MLB', sport='bs', home='뉴욕양키', away='보스레드',
        year=2026, date='10.26(월) 08:10', round=100,
        options=[dict(market='승패', label='', 선택=s, 배당=o, 시장확률=p, 게임번호='10')
                 for s, o, p in [('홈', 1.49, .52), ('원정', 2.1, .48)]])
    vote = dict(source='tailslips_public_html', event_type='first_observed', identity_version=2,
        eligible_pre_event=True, game_started=False, result=None, is_parlay_leg=False,
        is_baseline=False, market_type='moneyline', team_a='NYY', team_b='BOS',
        slug='synthetic-author', pick_id='synthetic-pick', selection='BOS ML +110', american_odds=110,
        slate_date='2026-10-25', game_header='NYY BOS 7:10 PM ET', observed_at=AT)
    records = []
    for scenario, side in [('missing_context', None), ('home_absences', 'home'), ('away_absences', 'away')]:
        changed = deepcopy(game)
        if side:
            changed['선발'] = dict(home='synthetic-home-starter', away='synthetic-away-starter',
                updated_at=AT, source='synthetic probe', source_url='https://example.test/fixture',
                unavailable={side: [{'name': 'synthetic-player', 'status': 'out', 'reason': 'injury'}]})
        snapshot = build_decision_snapshot(changed, as_of=AT, tipster_votes=[vote])
        validate_decision_snapshot(snapshot)
        evidence = snapshot['evidence']
        records.append(dict(scenario=scenario, selected=changed['추천']['선택'],
            probability=snapshot['probability']['final'],
            evidence=[{k: e.get(k) for k in ['id', 'available', 'usage']}
                      for e in evidence if e['id'] in {'lineup', 'availability'}]))
    return dict(synthetic=True, records=records,
        distinct_selections=len({r['selected'] for r in records}),
        explanation='Recognized context changes do not alter the current author-plurality selection.')


def temperature_probe(seed=20261029, rows=10000):
    """Positive temperature rescales confidence but preserves each row's argmax."""
    rng = np.random.default_rng(seed)
    trials = []
    for classes in [2, 3]:
        p = rng.dirichlet(np.ones(classes), rows)
        for temperature in [.5, 1., 2.]:
            calibrated = softmax(np.log(p) / temperature, axis=1)
            trials.append(dict(classes=classes, temperature=temperature, rows=rows,
                changed_selections=int(np.count_nonzero(p.argmax(1) != calibrated.argmax(1)))))
    return dict(synthetic=True, seed=seed, trials=trials,
        explanation='Does not apply to class-specific or intercept-shifting calibration; not a sports backtest.')


def disagreement_audit(path):
    """Re-score previously seen cohorts only; never fit/select a challenger."""
    raw = path.read_bytes()
    data = json.loads(raw)
    common = [e for e in data['event_ledger'] if all(p is not None for p in e['picks'].values())]
    stats = {}
    for agreement in [True, False]:
        batch = [e for e in common
                 if (e['picks']['tipster_vote'] == e['picks']['market']) == agreement]
        scored = [e for e in batch if not (e['winner'] == 'draw' and len(e['order']) == 2)]
        stats['agreement' if agreement else 'disagreement'] = dict(
            games=len(batch), pushes=len(batch)-len(scored),
            wins={m:sum(e['picks'][m] == e['winner'] for e in scored)
                  for m in ['market', 'tipster_vote', 'market90_vote10']})
    return dict(input_sha256_lf=hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest(),
        requested_window=data['requested_window'], common_games=len(common), groups=stats,
        multi_author_games=sum(e['voters'] >= 2 for e in common),
        future_independent_validation=False, new_predictive_model_tested=False)


def run(cohorts):
    return dict(production_selection=selection_probe(), temperature=temperature_probe(),
        cohorts={path.name:disagreement_audit(path) for path in cohorts},
        production_changes=False, new_accuracy_or_roi_improvement_claimed=False,
        scope='Existing-code mechanism probes and retrospective error decomposition only.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = run(args.cohort)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf8', newline='\n')
    print(json.dumps(dict(selection_variants=len(data['production_selection']['records']),
        selections=data['production_selection']['distinct_selections'], cohorts=data['cohorts']), ensure_ascii=False))
