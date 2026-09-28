"""Frozen, offline two-league experiment; never loads production storage.

Protocol committed as 67f09912 before strategy evaluation. Source CSVs are
downloaded from a fixed Git commit; computed reports include their SHA256.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
import hashlib
import io
from itertools import combinations, product
import json
from pathlib import Path
import sys
import urllib.request

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import softmax

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_external_temporal_roi import features
from scripts.evaluate_calibration_underdog import (
    logloss, probability_metrics, settle, summarize, paired_budget_delta,
)

REV = 'e82cf59161e82e0fbcdc49ad02819e45c821a5c8'
BASE = f'https://raw.githubusercontent.com/sosthene14/footballdataset/{REV}/datasets/'
LEAGUES = {'I1': 'seriea', 'F1': 'ligue1'}
COMPOSITIONS = {'FF': 0, 'FD': 1, 'DD': 2, 'any': None}
STRATEGIES = ('market', 'base', 'fixed_rank', 'conditional', 'relaxed_gate')
BOOKS = ('B365', 'BW', 'IW', 'PS', 'WH', 'VC')
CONDITIONS = ('all', 'home_underdog', 'away_underdog', 'draw_nonfavorite',
              'elo_advantage', 'form_advantage', 'rest_advantage')


def parse_price(value):
    try:
        value = float(value)
        return value if np.isfinite(value) and value > 1 else None
    except (ValueError, TypeError):
        return None


def parse_source(raw, league, year):
    rows, skipped = [], 0
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if not r.get('HomeTeam'):
            continue
        fmt = '%d/%m/%Y' if len(r['Date'].split('/')[-1]) == 4 else '%d/%m/%y'
        day = datetime.strptime(r['Date'], fmt).date()
        goals = int(r['FTHG']), int(r['FTAG'])
        y = 0 if goals[0] > goals[1] else 1 if goals[0] == goals[1] else 2
        if 'HDA'[y] != r['FTR'] or min(goals) < 0:
            raise ValueError('Invalid final result')
        prices = [parse_price(r.get('B365'+side)) for side in 'HDA']
        best = [max((v for book in BOOKS if (v := parse_price(r.get(book+side))) is not None),
                    default=None) for side in 'HDA']
        if None in prices:
            skipped += 1
            prices = None  # Still update future team history from this result.
        rows.append({'match_id': f"{league}:{day}:{r['HomeTeam']}:{r['AwayTeam']}",
                     'league': league, 'season': year, 'day': day, 'date': day.isoformat(),
                     'home_team': r['HomeTeam'], 'away_team': r['AwayTeam'],
                     'y': y, 'goals': goals, 'prices': prices, 'best': best})
    expected = 279 if league == 'F1' and year == 19 else 306 if league == 'F1' and year >= 23 else 380
    if len(rows) != expected or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError(f'Coverage/duplicate failure {league}/{year}: {len(rows)}')
    if any(not (2000+year <= r['day'].year <= 2001+year) for r in rows):
        raise ValueError('Season/date mismatch')
    return rows, skipped


def download():
    def one(item):
        league, year = item
        path = f'{LEAGUES[league]}/{year:02}{year+1:02}_{league}.csv'
        raw = urllib.request.urlopen(BASE+path, timeout=30).read()
        rows, skipped = parse_source(raw, league, year)
        return rows, {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'matches': len(rows), 'missing_b365': skipped}
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(one, [(league, year) for league in LEAGUES for year in range(16, 25)]))
    rows = [r for data, _ in values for r in data]
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Cross-file duplicate')
    return rows, [meta for _, meta in values]


def add_features(rows):
    """Per-league Elo/form are delayed >7 days. Rest never uses current results."""
    output = []
    for league in sorted({r['league'] for r in rows}):
        source = sorted((r for r in rows if r['league'] == league), key=lambda r: (r['day'], r['match_id']))
        dates = defaultdict(list)
        enriched = []
        for row in source:
            rests = []
            for team in (row['home_team'], row['away_team']):
                previous = [d for d in dates[team] if d < row['day']]
                rests.append(min(14, (row['day']-previous[-1]).days) if previous else 14)
            enriched.append({**row, 'rest_gap': (rests[0]-rests[1])/14})
            for team in (row['home_team'], row['away_team']):
                dates[team].append(row['day'])
        output.extend(features(enriched))
    return sorted(output, key=lambda r: (r['day'], r['match_id']))


def design(rows, conditional):
    x = np.asarray([r['x'][:3] for r in rows])
    base = np.einsum('nf,ij->nifj', x, np.eye(3)).reshape(len(rows), 3, 9)
    if not conditional:
        return base
    extra = []
    for r in rows:
        dog = np.asarray(r['prices']) > min(r['prices'])
        direction = np.array([1., 0., -1.])
        extra.append(np.stack([dog, dog*np.array([1, 0, 0]), dog*np.array([0, 1, 0]),
                               dog*direction*r['x'][1], dog*direction*r['x'][2],
                               dog*direction*r['rest_gap']], axis=1))
    return np.concatenate([base, np.asarray(extra)], axis=2)


def fit_model(train, calibration, conditional):
    if max(r['day'] for r in train) >= min(r['day'] for r in calibration):
        raise ValueError('Training/calibration overlap')
    x = design(train, conditional)
    market = np.asarray([r['market'] for r in train])
    y = np.asarray([r['y'] for r in train])
    target = np.eye(3)[y]
    def objective(w):
        logits = np.log(market)+x@w
        grad = np.einsum('nif,ni->f', x, softmax(logits, axis=1)-target)/len(y)+.1*w
        return logloss(logits, y)+.05*np.square(w).sum(), grad
    result = minimize(objective, np.zeros(x.shape[-1]), method='L-BFGS-B', jac=True)
    if not result.success:
        raise RuntimeError(result.message)
    logits = np.log([r['market'] for r in calibration])+design(calibration, conditional)@result.x
    cy = np.asarray([r['y'] for r in calibration])
    temp = minimize_scalar(lambda t: logloss(logits/t, cy), bounds=(.5, 2.), method='bounded')
    if not temp.success:
        raise RuntimeError(temp.message)
    return {'weights': result.x.tolist(), 'temperature': float(temp.x), 'conditional': conditional}


def predict(model, rows):
    logits = np.log([r['market'] for r in rows])+design(rows, model['conditional'])@model['weights']
    return softmax(logits/model['temperature'], axis=1)


def choose(rows, probs, composition, strategy, gate, price_mode):
    """No outcome access: bonus is a ranking term, never a probability."""
    if composition not in COMPOSITIONS or strategy not in STRATEGIES or gate not in ('ev03', 'control') or price_mode not in ('b365', 'best_scenario'):
        raise ValueError('Unknown experiment setting')
    probs = np.asarray(probs)
    if probs.shape != (len(rows), 3) or not np.isfinite(probs).all() or (probs <= 0).any() or not np.allclose(probs.sum(axis=1), 1):
        raise ValueError('Invalid probabilities')
    if len({r['match_id'] for r in rows}) != len(rows) or len({r['league'] for r in rows}) != 1:
        raise ValueError('Duplicate or mixed-league candidates')
    days = defaultdict(list)
    for row, probabilities in zip(rows, probs):
        options = []
        prices = row['prices'] if price_mode == 'b365' else row['best']
        if any(parse_price(o) is None for o in prices):
            raise ValueError('Invalid odds')
        for i, (p, odds) in enumerate(zip(probabilities, prices)):
            dog = bool(row['prices'][i] > min(row['prices']))
            ev = float(p*odds-1)
            gate_ev = ev + (.03 if dog and strategy == 'relaxed_gate' else 0)
            if gate == 'ev03' and gate_ev < .03:
                continue
            options.append({'id': row['match_id'], 'choice': i, 'probability': float(p),
                            'odds': odds, 'ev': ev, 'nonfavorite': dog})
        days[row['date']].append(options)
    tickets = {}
    for day, events in sorted(days.items()):
        ranked = []
        for first, second in combinations(events, 2):
            for a, b in product(first, second):
                dogs = int(a['nonfavorite'])+int(b['nonfavorite'])
                if COMPOSITIONS[composition] is not None and dogs != COMPOSITIONS[composition]:
                    continue
                score = np.log1p(a['ev'])+np.log1p(b['ev'])
                if strategy in ('fixed_rank', 'relaxed_gate'):
                    score += .03*dogs
                ranked.append((-float(score), a['id'], a['choice'], b['id'], b['choice'], [a, b]))
        tickets[day] = min(ranked, key=lambda t: t[:5])[-1] if ranked else []
    return tickets


def signature(pair):
    return tuple(sorted((leg['id'], leg['choice']) for leg in pair))


def selection_diagnostics(rows, tickets):
    """Separate price improvement on identical picks from price-driven reselection."""
    lookup = {r['match_id']: r for r in rows}
    repriced = {day: [{**leg, 'odds': lookup[leg['id']]['best'][leg['choice']]} for leg in pair]
                for day, pair in tickets.items()}
    records = settle(rows, repriced)
    n = sum(r['stake'] for r in records)
    pairs = [pair for pair in tickets.values() if pair]
    return {'same_picks_best_price_raw_roi': sum(r['raw'] for r in records)/n if n else None,
            'draw_legs': sum(leg['choice'] == 1 for pair in pairs for leg in pair),
            'pairs_with_draw': sum(any(leg['choice'] == 1 for leg in pair) for pair in pairs),
            'pairs_without_draw': sum(all(leg['choice'] != 1 for leg in pair) for pair in pairs)}


def incremental(rows, tickets, baseline):
    changed = {day: pair if pair and signature(pair) != signature(baseline[day]) else []
               for day, pair in tickets.items()}
    summary = summarize(rows, changed, settle(rows, changed))
    summary['newly_bet_days'] = sum(bool(pair) and not baseline[d] for d, pair in tickets.items())
    summary['replaced_days'] = sum(bool(pair) and bool(baseline[d]) for d, pair in changed.items())
    return summary


def condition_stats(rows, probs):
    """Diagnostic single-leg strata, not an out-of-sample selected policy."""
    result = {}
    for name in CONDITIONS:
        selected = []
        for r, p in zip(rows, probs):
            for i, odds in enumerate(r['prices']):
                if odds <= min(r['prices']):
                    continue
                direction = (1, 0, -1)[i]
                passes = {'all': True, 'home_underdog': i == 0, 'away_underdog': i == 2,
                          'draw_nonfavorite': i == 1, 'elo_advantage': direction*r['x'][1] > 0,
                          'form_advantage': direction*r['x'][2] > 0,
                          'rest_advantage': direction*r['rest_gap'] > 0}[name]
                if passes:
                    selected.append((int(r['y'] == i), odds, float(p[i])))
        n = len(selected)
        result[name] = {'legs': n, 'hit_rate': sum(a for a, _, _ in selected)/n if n else None,
                        'roi_raw': sum(a*b-1 for a, b, _ in selected)/n if n else None,
                        'predicted_probability': sum(p for _, _, p in selected)/n if n else None}
    return result


def run():
    raw, sources = download()
    data = add_features(raw)
    report = {'protocol_commit': '67f09912', 'source_commit': REV, 'sources': sources,
              'production_policy_changed': False, 'models': {}, 'groups': {}, 'pooled': {}}
    pooled = defaultdict(list)
    for league in LEAGUES:
        train = [r for r in data if r['league'] == league and 19 <= r['season'] <= 21]
        calibration = [r for r in data if r['league'] == league and r['season'] == 22]
        models = {name: fit_model(train, calibration, conditional) for name, conditional in [('base', False), ('conditional', True)]}
        report['models'][league] = {'train': len(train), 'calibration': len(calibration), **models}
        for year in (23, 24):
            rows = [r for r in data if r['league'] == league and r['season'] == year]
            if max(r['day'] for r in calibration) >= min(r['day'] for r in rows):
                raise ValueError('Calibration/test overlap')
            ps = {name: predict(model, rows) for name, model in models.items()}
            ps['market'] = np.asarray([r['market'] for r in rows])
            group = {'matches': len(rows), 'probability': {k: probability_metrics(rows, p) for k, p in ps.items()},
                     'conditions': condition_stats(rows, ps['conditional']), 'strategies': {}}
            for price_mode, gate, composition in product(('b365', 'best_scenario'), ('ev03', 'control'), COMPOSITIONS):
                baseline = choose(rows, ps['base'], composition, 'base', gate, price_mode)
                baseline_daily = settle(rows, baseline)
                for strategy in STRATEGIES:
                    p = ps[strategy] if strategy in ps else ps['base']
                    tickets = choose(rows, p, composition, strategy, gate, price_mode)
                    daily = settle(rows, tickets)
                    key = '/'.join((price_mode, gate, composition, strategy))
                    group['strategies'][key] = {'summary': summarize(rows, tickets, daily),
                                               'delta_vs_base': paired_budget_delta(daily, baseline_daily),
                                               'incremental': incremental(rows, tickets, baseline),
                                               'selection_diagnostics': selection_diagnostics(rows, tickets)}
                    pooled[key].append((rows, tickets, daily, baseline_daily, baseline))
            report['groups'][f'{league}/{year}'] = group
    for key, groups in pooled.items():
        # Each league-date has its own 1-unit budget; shared ISO weeks stay clustered.
        rows = [r for g in groups for r in g[0]]
        daily = sorted([r for g in groups for r in g[2]], key=lambda r: r['date'])
        base_daily = sorted([r for g in groups for r in g[3]], key=lambda r: r['date'])
        # summarize only uses ticket values/hash, so unique group keys prevent date collisions.
        tickets = {f'{idx}:{day}': legs for idx, g in enumerate(groups) for day, legs in g[1].items()}
        summary = summarize(rows, tickets, daily)
        delta = paired_budget_delta(daily, base_daily)
        season_profit = {str(year): sum(r['raw'] for g in groups if g[0][0]['season'] == year for r in g[2]) for year in (23, 24)}
        changes, changed_daily, new_days, replaced_days = {}, [], 0, 0
        for idx, (part_rows, part_tickets, _, _, baseline) in enumerate(groups):
            changed = {day: pair if pair and signature(pair) != signature(baseline[day]) else []
                       for day, pair in part_tickets.items()}
            changes.update({f'{idx}:{day}': pair for day, pair in changed.items()})
            changed_daily.extend(settle(part_rows, changed))
            new_days += sum(bool(pair) and not baseline[day] for day, pair in changed.items())
            replaced_days += sum(bool(pair) and bool(baseline[day]) for day, pair in changed.items())
        changed_summary = summarize(rows, changes, sorted(changed_daily, key=lambda r: r['date']))
        changed_summary.update(newly_bet_days=new_days, replaced_days=replaced_days)
        diagnostics = [selection_diagnostics(g[0], g[1]) for g in groups]
        same_pick_profit = sum((d['same_picks_best_price_raw_roi'] or 0)*sum(r['stake'] for r in g[2])
                               for d, g in zip(diagnostics, groups))
        report['pooled'][key] = {'summary': summary, 'delta_vs_base': delta, 'season_profit': season_profit,
                                'incremental': changed_summary,
                                'selection_diagnostics': {
                                    'same_picks_best_price_raw_roi': same_pick_profit/summary['tickets'] if summary['tickets'] else None,
                                    **{name: sum(d[name] for d in diagnostics) for name in ('draw_legs', 'pairs_with_draw', 'pairs_without_draw')}},
                                'unadjusted_screen_only': bool(summary['tickets'] >= 100 and
                                    summary['roi_raw_ci95_week_cluster'][0] > 0 and
                                    delta['ci95_week_cluster'][0] > 0 and min(season_profit.values()) > 0)}
    report['limitations'] = ['Exploratory multiple comparisons: no confirmatory winner or production promotion.',
                            'Missing timestamped injuries, lineup, rotations and odds movement.',
                            'League-only rest; odds observation times and simultaneous best-price execution unknown.',
                            'Foreign odds with hypothetical Korean rounding are not realized Proto returns.',
                            'Predicted pair EV assumes independence; estimated probabilities are not known true probabilities.']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = run()
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({'groups': {k: v['matches'] for k, v in report['groups'].items()},
                      'settings': len(report['pooled']),
                      'unadjusted_screen': [k for k, v in report['pooled'].items() if v['unadjusted_screen_only']]}, ensure_ascii=False))
