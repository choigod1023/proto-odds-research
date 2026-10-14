"""Local, read-only challenger review. Never changes or deploys a model.

Consumes the research fold/forecast/ledger format. These retrospective artifacts
are deliberately never certified as prospective production evidence.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def metrics(records):
    # Sum concurrent budgets before calculating the drawdown path.
    daily = defaultdict(float)
    for r in records:
        daily[r['date']] += r['profit']
    balance = peak = drawdown = 0.
    losses = longest = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
        if daily[day] < 0:
            losses += 1
        elif daily[day] > 0:
            losses = 0
        longest = max(longest, losses)
    stake = sum(r['stake'] for r in records)
    return dict(budgets=len(records), tickets=stake, wins=sum(r['won'] for r in records),
        profit=sum(r['profit'] for r in records),
        roi=sum(r['profit'] for r in records)/stake if stake else None,
        coverage=stake/len(records) if records else 0.,
        max_drawdown_units=drawdown, longest_losing_active_days=longest)


def extract(artifact, models, policy, as_of):
    """As-of review uses only results released under conservative date+7 rule."""
    cutoff = (date.fromisoformat(as_of)-timedelta(days=7)).isoformat()
    output = {m: [] for m in models}
    seen = set()
    expected = set()
    for fold in artifact['folds']:
        forecasts = {r['match_id']: r for r in fold['forecasts']}
        if len(forecasts) != len(fold['forecasts']):
            raise ValueError('duplicate forecasts')
        expected.update((r['date'], r['league'], fold['outer_year']) for r in forecasts.values() if r['date'] <= cutoff)
        for model in models:
            for record in fold['ledgers'][model][policy]:
                day = date.fromisoformat(record['date']).isoformat()
                if day > cutoff:
                    continue  # Do not inspect unreleased outcome/profit.
                key = (model, record['league'], day)
                if key in seen:
                    raise ValueError('duplicate model budget')
                seen.add(key)
                legs = record['legs']
                if len(legs) not in (0, 2) or len({x['match_id'] for x in legs}) != len(legs):
                    raise ValueError('two distinct matches required')
                payout, won = 1., bool(legs)
                for leg in legs:
                    match = forecasts[leg['match_id']]
                    outcome = leg['outcome']
                    if type(outcome) is not int or outcome not in (0, 1, 2):
                        raise ValueError('invalid outcome')
                    if (match['league'], match['date']) != (record['league'], day):
                        raise ValueError('budget mismatch')
                    price = match['prices'][outcome]
                    if type(match['y']) is not int or match['y'] not in (0, 1, 2):
                        raise ValueError('invalid actual outcome')
                    if not math.isfinite(price) or price <= 1 or price != leg['odds']:
                        raise ValueError('invalid settlement price')
                    payout *= price
                    won = won and match['y'] == outcome
                profit = (payout if won else 0.)-int(bool(legs))
                if record['budget'] != 1 or record['stake'] != int(bool(legs)) or record['won'] != int(won):
                    raise ValueError('invalid settlement flags')
                if not math.isfinite(record['profit']) or abs(record['profit']-profit) > 1e-9:
                    raise ValueError('settlement mismatch')
                output[model].append(dict(record, season=fold['outer_year']))
    for model in models:
        output[model].sort(key=lambda r: (r['date'], r['league']))
    keys = [[(r['date'], r['league'], r['season']) for r in output[m]] for m in models]
    if any(k != keys[0] for k in keys):
        raise ValueError('unpaired budgets')
    if set(keys[0]) != expected:
        raise ValueError('missing or extra source budgets')
    return output


def bounds(candidate, champion, *, comparisons, looks, reps=10000):
    """Joint ISO-week resampling. Bonferroni over candidates, looks, 2 tests.

    Two predeclared tests: own ROI>0 and paired ROI improvement>0.
    Does not correct unregistered prior research or data-dependent redesign.
    """
    weeks = defaultdict(lambda: np.zeros(4))
    for a, b in zip(candidate, champion):
        iso = date.fromisoformat(a['date']).isocalendar()
        weeks[(iso.year, iso.week)] += [a['profit'], a['stake'], b['profit'], b['stake']]
    if len(weeks) < 2:
        return dict(weeks=len(weeks), roi_lower=None, improvement_lower=None, valid_replicates=0)
    matrix = np.array([weeks[k] for k in sorted(weeks)])
    rng = np.random.default_rng(20261002)
    sums = np.concatenate([matrix[rng.integers(len(matrix), size=(min(250, reps-i), len(matrix)))].sum(axis=1)
                           for i in range(0, reps, 250)])
    valid = (sums[:, 1] > 0) & (sums[:, 3] > 0)
    tail = .05/(comparisons*looks*2)
    # Do not report a gate from unsupported extreme empirical quantiles.
    if valid.sum() < reps*.99 or valid.sum()*tail < 20:
        return dict(weeks=len(weeks), roi_lower=None, improvement_lower=None,
                    valid_replicates=int(valid.sum()), tail=tail)
    roi, baseline = sums[valid, 0]/sums[valid, 1], sums[valid, 2]/sums[valid, 3]
    return dict(weeks=len(weeks), roi_lower=float(np.quantile(roi, tail)),
        improvement_lower=float(np.quantile(roi-baseline, tail)), valid_replicates=int(valid.sum()), tail=tail)


def review(artifact, champion, candidates, policy, as_of, *, planned_looks=1, minimum_tickets=300, reps=10000):
    if not candidates or len(set(candidates)) != len(candidates) or champion in candidates:
        raise ValueError('unique candidates distinct from champion required')
    if type(planned_looks) is not int or planned_looks < 1 or type(minimum_tickets) is not int or minimum_tickets < 300:
        raise ValueError('invalid review policy')
    if type(reps) is not int or not 1000 <= reps <= 100000:
        raise ValueError('invalid bootstrap count')
    ledgers = extract(artifact, [champion]+candidates, policy, as_of)
    reference = ledgers[champion]
    baseline = metrics(reference)
    reports = {}
    for model in candidates:
        records = ledgers[model]
        measured = metrics(records)
        interval = bounds(records, reference, comparisons=len(candidates), looks=planned_looks, reps=reps)
        strata = {}
        for league, season in sorted({(r['league'], r['season']) for r in records}):
            part = [r for r in records if (r['league'], r['season']) == (league, season)]
            old = [r for r in reference if (r['league'], r['season']) == (league, season)]
            strata[f'{league}:{season}'] = dict(candidate=metrics(part), champion=metrics(old))
        checks = dict(minimum_sample=min(measured['tickets'], baseline['tickets']) >= minimum_tickets,
            minimum_weeks=interval['weeks'] >= 26,
            multiple_seasons=len({r['season'] for r in records}) >= 2,
            positive_roi_lower=interval['roi_lower'] is not None and interval['roi_lower'] > 0,
            positive_improvement_lower=interval['improvement_lower'] is not None and interval['improvement_lower'] > 0,
            coverage_preserved=measured['coverage'] >= baseline['coverage'],
            drawdown_not_worse=measured['max_drawdown_units'] <= baseline['max_drawdown_units'],
            losing_run_not_worse=measured['longest_losing_active_days'] <= baseline['longest_losing_active_days'],
            strata_stable=bool(strata) and all(s['candidate']['tickets'] >= 30 and s['champion']['tickets'] >= 30
                and s['candidate']['roi'] > 0 and s['candidate']['roi'] >= s['champion']['roi'] for s in strata.values()))
        reports[model] = dict(metrics=measured, bounds=interval, strata=strata, checks=checks,
            historical_screen_passed=all(checks.values()),
            status='research_followup_only' if all(checks.values()) else 'hold',
            blockers=[k for k,v in checks.items() if not v]+['prospective_probability_and_provenance_review_required'])
    return dict(schema='model-review-v1', as_of=as_of, champion=champion, policy=policy,
        evidence_type='retrospective_research', production_action='none', approval_required=True,
        recommendation='keep_production_configuration; not a profitability endorsement',
        baseline=baseline, candidates=reports,
        screen_policy=dict(minimum_tickets=minimum_tickets, minimum_weeks=26, minimum_stratum_tickets=30,
            planned_looks=planned_looks, registered_candidates=candidates, reps=reps, seed=20261002),
        limitations=['Thresholds are engineering screening defaults, not validated profitability guarantees.',
            'Correction covers only this registered candidate/look family, not past experiments.',
            'Date+7 is a retrospective availability assumption, not certified recording timestamps.',
            'No automatic training, production certification, approval or deployment.'])


def replay(artifact, champion, candidates, policy, review_dates, end):
    """Counterfactual selector test, not authorization to switch production.

    Every challenger must beat the fixed incumbent. Decisions take effect only
    on/after that checkpoint. No final-period winner is selected in hindsight.
    """
    if not review_dates or review_dates != sorted(set(review_dates)):
        raise ValueError('predeclared unique chronological review dates required')
    for day in review_dates+[end]:
        date.fromisoformat(day)
    if end <= review_dates[-1]:
        raise ValueError('end must follow final review')
    decisions = []
    for checkpoint in review_dates:
        result = review(artifact, champion, candidates, policy, checkpoint, planned_looks=len(review_dates))
        eligible = [m for m in candidates if result['candidates'][m]['historical_screen_passed']]
        chosen = min(eligible, key=lambda m: (-result['candidates'][m]['bounds']['improvement_lower'], m)) if eligible else champion
        decisions.append(dict(as_of=checkpoint, shadow_model=chosen, review=result))
    all_records = extract(artifact, [champion]+candidates, policy, end)
    selected, fixed = [], []
    for index, record in enumerate(all_records[champion]):
        if record['date'] < review_dates[0]:
            continue
        decision = next(d for d in reversed(decisions) if d['as_of'] <= record['date'])
        selected.append(dict(all_records[decision['shadow_model']][index], selected_model=decision['shadow_model']))
        fixed.append(record)
    return dict(schema='shadow-selector-replay-v1', production_action='none', approval_required=True,
        warning='Exposed retrospective counterfactual; not prospective strategy validation.',
        decisions=decisions, selected_metrics=metrics(selected), fixed_metrics=metrics(fixed), ledger=selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--champion', default='shin')
    parser.add_argument('--candidates', nargs='+', required=True)
    parser.add_argument('--policy', default='highestprob')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--planned-looks', type=int, default=1)
    parser.add_argument('--replay-dates', nargs='+', help='Predeclared checkpoints; --as-of becomes evaluation end')
    args = parser.parse_args()
    raw = args.input.read_bytes()
    artifact = json.loads(raw)
    result = (replay(artifact, args.champion, args.candidates, args.policy, args.replay_dates, args.as_of)
              if args.replay_dates else review(artifact, args.champion, args.candidates, args.policy, args.as_of, planned_looks=args.planned_looks))
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    # Exclusive output prevents silent overwrite of prior review evidence.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k:v['status'] for k,v in result['candidates'].items()} if 'candidates' in result
                     else {'decisions':len(result['decisions']), 'production_action':'none'}))


if __name__ == '__main__':
    main()
