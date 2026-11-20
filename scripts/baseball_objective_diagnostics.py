"""Separate forecast calibration, hit selection and price-aware betting objectives."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'experiments/baseball-information-stepchange-20261029/diagnostic'
SPLIT = '2024-07-01'
POLICIES = ('hit', 'value', 'edge0', 'edge3', 'edge6', 'edge10')


def read(path):
    return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def write(path, value, lines=False):
    text = ''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in value) if lines else json.dumps(value, ensure_ascii=False, indent=2)+'\n'
    path.write_text(text, encoding='utf8', newline='\n')


def fit_calibrator(records):
    if not records or any(r['role'] != 'validation' or r['date'] >= SPLIT for r in records):
        raise ValueError('calibration requires early validation only')
    settled = [r for r in records if r['hg'] != r['ag']]
    x = np.array([logit(np.clip(r['model_probability'], 1e-8, 1-1e-8)) for r in settled])
    y = np.array([r['hg'] > r['ag'] for r in settled], float)

    def objective(beta):
        z = beta[0]+beta[1]*x
        residual = expit(z)-y
        offset = beta-np.array([0., 1.])
        return (float(np.mean(np.logaddexp(0, z)-y*z)+.005*np.dot(offset, offset)),
                np.array([residual.mean(), np.mean(residual*x)])+.01*offset)

    fit = minimize(objective, np.array([0., 1.]), jac=True, method='L-BFGS-B',
                   options={'maxiter': 1000, 'ftol': 1e-12, 'gtol': 1e-9})
    if not fit.success:
        raise ValueError('calibration failed:'+str(fit.message))
    return dict(beta=fit.x.tolist(), n=len(settled), first=min(r['date'] for r in records),
                last=max(r['date'] for r in records), penalty=.01)


def probability(row, kind, calibrator):
    if kind == 'market':
        return row['market_probability']
    p = row['model_probability']
    if kind == 'primary':
        return p
    b = calibrator['beta']
    return float(expit(b[0]+b[1]*logit(np.clip(p, 1e-8, 1-1e-8))))


def decide(row, p, policy):
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('invalid probability')
    prices, order = row['prices'], row['order']
    if len(prices) != 2 or set(order) != {'home', 'away'} or any(not math.isfinite(o) or o <= 1 for o in prices):
        raise ValueError('invalid prices')
    home, away = [prices[order.index(s)] for s in ('home', 'away')]
    if policy == 'hit':
        side = 'home' if p >= .5 else 'away'
    else:
        side = 'home' if p*home >= (1-p)*away else 'away'
    chosen_p = p if side == 'home' else 1-p
    ev = chosen_p*prices[order.index(side)]-1
    if policy.startswith('edge') and ev <= int(policy[4:])/100:
        return None
    return side


def settle(row, side):
    if side is None or row['hg'] == row['ag']:
        return 0
    if (row['hg'] > row['ag']) != (side == 'home'):
        return -10000
    payout = (Decimal(str(row['prices'][row['order'].index(side)]))*10000).to_integral_value(rounding=ROUND_DOWN)
    return int(payout)-10000


def measures(records, probabilities, sides):
    if not len(records) == len(probabilities) == len(sides):
        raise ValueError('misaligned decisions')
    daily, monthly = defaultdict(int), defaultdict(int)
    net = wins = pushes = bets = 0
    brier = loss = 0.
    for row, p, side in zip(records, probabilities, sides):
        profit = settle(row, side)
        net += profit
        daily[row['date']] += profit
        monthly[row['date'][:7]] += profit
        if side is None:
            continue
        bets += 1
        if row['hg'] == row['ag']:
            pushes += 1
            continue
        wins += int((row['hg'] > row['ag']) == (side == 'home'))
        y = int(row['hg'] > row['ag'])
        brier += (p-y)**2
        p = min(1-1e-9, max(1e-9, p))
        loss -= math.log(p if y else 1-p)
    balance = peak = drawdown = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
    return dict(opportunities=len(records), bets=bets, coverage=bets/len(records) if records else None,
                wins=wins, pushes=pushes, accuracy=wins/(bets-pushes) if bets > pushes else None,
                net_krw=net, roi=net/(bets*10000) if bets else None,
                opportunity_roi=net/(len(records)*10000) if records else None,
                brier=brier/(bets-pushes) if bets > pushes else None,
                logloss=loss/(bets-pushes) if bets > pushes else None,
                monthly_net_krw=dict(monthly), daily_close_max_drawdown_krw=drawdown)


def policies(records, calibrator):
    result, ledger = {}, []
    for kind in ('market', 'primary', 'calibrated'):
        ps = [probability(r, kind, calibrator) for r in records]
        for policy in POLICIES:
            name = kind+':'+policy
            sides = [decide(r, p, policy) for r, p in zip(records, ps)]
            result[name] = measures(records, ps, sides)
            ledger.append((name, ps, sides))
    return result, ledger


def choose_validation(metrics, n):
    minimum = max(25, math.ceil(n*.1))
    eligible = [k for k, v in metrics.items() if v['bets'] >= minimum and v['net_krw'] > 0 and v['roi'] > 0]
    name = min(eligible, key=lambda k: (-metrics[k]['roi'], -metrics[k]['net_krw'], -metrics[k]['bets'], k)) if eligible else None
    return dict(selected=name, minimum_bets=minimum, eligible=eligible,
                reason='positive validation ROI and minimum participation' if name else 'no policy met validation profitability and coverage gate')


def economic_audit(records):
    baseline_p = [r['baseline_probability'] for r in records]
    baseline_sides = [decide(r, p, 'hit') for r, p in zip(records, baseline_p)]
    metric = measures(records, baseline_p, baseline_sides)
    overround, assumed_ev, missed_improvements, gaps, minimum_weights = [], [], [], [], []
    unanimous_cannot_clear_price = 0
    for r, side in zip(records, baseline_sides):
        prices = r['prices']
        overround.append(sum(1/o for o in prices)-1)
        p = r['market_probability'] if side == 'home' else 1-r['market_probability']
        odds = prices[r['order'].index(side)]
        assumed_ev.append(p*odds-1)
        gaps.append(1/odds-p)
        m = r['market_probability']
        side_ps = [m if s == 'home' else 1-m for s in r['order']]
        # Perfectly unanimous votes in either direction are an optimistic upper
        # bound on what a fixed market90/vote10 probability can represent.
        maximum_evs = [(.9*q+.1)*o-1 for q, o in zip(side_ps, prices)]
        unanimous_cannot_clear_price += int(max(maximum_evs) <= 0)
        minimum_weights.append(min(max(0., (1/o-q)/(1-q)) for q, o in zip(side_ps, prices)))
        if settle(r, side) == -10000:
            true_side = 'home' if r['hg'] > r['ag'] else 'away'
            missed_improvements.append(settle(r, true_side)+10000)
    need = -metric['net_krw']
    cumulative = 0
    oracle_count = 0 if need <= 0 else None
    for i, gain in enumerate(sorted(missed_improvements, reverse=True), 1):
        cumulative += gain
        if oracle_count is None and cumulative >= need:
            oracle_count = i
    return dict(baseline=metric, mean_overround=float(np.mean(overround)),
                mean_market_assumed_ev=float(np.mean(assumed_ev)),
                mean_market_probability_to_chosen_odds_break_even_gap=float(np.mean(gaps)),
                fixed_9to1_even_unanimous_cannot_clear_price_n=unanimous_cannot_clear_price,
                minimum_unanimous_vote_weight_any_side_quantiles=np.quantile(minimum_weights, [.25, .5, .75]).tolist(),
                fixed_weight_note='representational upper bound under market probabilities; increasing vote weight does not prove calibration or profit',
                oracle_min_wrong_pick_flips_to_break_even=oracle_count,
                oracle_note='optimistic hindsight lower bound: choose highest-paying missed actual winners first; not attainable prediction claim')


def paired_interval(records, ps, sides, repetitions=3000):
    weeks = defaultdict(list)
    for i, r in enumerate(records):
        day = date.fromisoformat(r['date'])
        weeks[(day-timedelta(days=day.weekday())).isoformat()].append(i)
    cells = []
    for ids in weeks.values():
        chosen_net = sum(settle(records[i], sides[i]) for i in ids)
        base_net = sum(settle(records[i], decide(records[i], records[i]['baseline_probability'], 'hit')) for i in ids)
        cells.append([len(ids), sum(sides[i] is not None for i in ids), chosen_net, chosen_net-base_net])
    cells = np.array(cells, float)
    rng = np.random.default_rng(20261029)
    sums = cells[rng.integers(len(cells), size=(repetitions, len(cells)))].sum(axis=1)
    valid = sums[:, 1] > 0
    return dict(weeks=len(weeks), repetitions=repetitions,
                selected_roi95=np.quantile(sums[valid, 2]/sums[valid, 1]/10000, [.025, .975]).tolist() if valid.any() else None,
                opportunity_roi_delta95=np.quantile(sums[:, 3]/sums[:, 0]/10000, [.025, .975]).tolist(),
                note='descriptive paired weekly resampling; no correction for repeated research or policy selection')


def run(folder):
    frozen, inputs = {}, {}
    # Complete calibration and late-validation selection for BOTH leagues before evaluation.
    for league in ('mlb', 'kbo'):
        records = read(folder/(league+'-inputs.jsonl'))
        validation = [r for r in records if r['role'] == 'validation']
        early = [r for r in validation if r['date'] < SPLIT]
        late = [r for r in validation if r['date'] >= SPLIT]
        cal = fit_calibrator(early)
        scores, _ = policies(late, cal)
        frozen[league] = dict(calibrator=cal, validation_n=len(late), validation=scores,
                              selection=choose_validation(scores, len(late)))
        inputs[league] = records
    write(folder/'frozen-policy.json', frozen)
    output, ledger = {}, []
    for league, records in inputs.items():
        groups = defaultdict(list)
        for row in records:
            if row['role'] == 'evaluation':
                groups[row['eval_set']].append(row)
        output[league] = {}
        for group, records in groups.items():
            split = '2026-08-10' if group == 'external230' else '2026-07-01'
            panels = dict(all=records, before_split=[r for r in records if r['date'] < split],
                          split_onward=[r for r in records if r['date'] >= split])
            output[league][group] = {}
            for period, panel in panels.items():
                scores, policies_ledger = policies(panel, frozen[league]['calibrator'])
                selected = frozen[league]['selection']['selected']
                summary = dict(economic_audit=economic_audit(panel), policies=scores, selected=selected,
                               selected_metrics=scores[selected] if selected else None)
                if selected:
                    _, ps, sides = next(x for x in policies_ledger if x[0] == selected)
                    summary['selected_paired_interval'] = paired_interval(panel, ps, sides)
                output[league][group][period] = summary
                if period == 'all':
                    # These are predeclared diagnostic hypotheses, NOT alternative winners
                    # selected using the evaluation results.
                    summary['diagnostic_intervals'] = {
                        name: paired_interval(panel, ps, sides)
                        for name, ps, sides in policies_ledger if not name.startswith('market:')}
                    for i, row in enumerate(panel):
                        ledger.append(dict(row, policies={name:dict(probability=ps[i], side=sides[i],
                                           net_krw=settle(row, sides[i])) for name, ps, sides in policies_ledger}))
    write(folder/'evaluation-policies.jsonl', ledger, lines=True)
    write(folder/'results.json', dict(frozen=frozen, evaluation=output, input_sha256={
        (l+'-inputs.jsonl'):hashlib.sha256((folder/(l+'-inputs.jsonl')).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        for l in inputs}, note='previous frozen primary; retrospective policy study; no monthly cap; not a new independently fitted forecast'))
    for league, spec in frozen.items():
        print(league, 'calibrator', spec['calibrator'], 'selection', spec['selection'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEFAULT)
    run(parser.parse_args().folder)
