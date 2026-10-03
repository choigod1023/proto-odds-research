"""Single frozen external replication; no production I/O. Protocol cbb8ef02."""
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
import platform
import subprocess
import sys
import urllib.request

import numpy as np
import scipy
from scipy.optimize import brentq
from scipy.special import expit, logit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.devig import shin
from scripts.evaluate_external_temporal_roi import payout

REV = 'e82cf59161e82e0fbcdc49ad02819e45c821a5c8'
LEAGUES = {'D1': 'bundesliga1', 'SP1': 'laliga'}
SEED, REPS = 20261001, 5000
PROTOCOL = 'docs/research/2026-10-01-draw-calibration-protocol.md'


def parse_source(raw, league, year):
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if not r.get('HomeTeam'):
            continue
        if r.get('Div') != league:
            raise ValueError('League mismatch')
        day = datetime.strptime(r['Date'], '%d/%m/%Y' if len(r['Date'].split('/')[-1]) == 4 else '%d/%m/%y').date()
        if not datetime(2000+year, 7, 1).date() <= day < datetime(2001+year, 7, 1).date():
            # COVID delayed 2019/20 through summer 2020; still before next season.
            if not (year == 19 and day < datetime(2020, 9, 1).date() and day.year == 2020):
                raise ValueError('Season/date mismatch')
        goals = int(r['FTHG']), int(r['FTAG'])
        y = 0 if goals[0] > goals[1] else 1 if goals[0] == goals[1] else 2
        if min(goals) < 0 or 'HDA'[y] != r['FTR']:
            raise ValueError('Invalid final result')
        try:
            prices = [float(r['B365'+s]) for s in 'HDA']
            if any(not np.isfinite(v) or v <= 1 for v in prices):
                prices = None
        except (KeyError, ValueError, TypeError):
            prices = None
        rows.append(dict(match_id=f"{league}:{day}:{r['HomeTeam']}:{r['AwayTeam']}",
                         league=league, season=year, date=day.isoformat(), y=y, prices=prices))
    if len(rows) != (306 if league == 'D1' else 380):
        raise ValueError(f'Coverage failure: {league}/{year}: {len(rows)}')
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate match')
    return rows


def download():
    def one(item):
        league, year = item
        path = f'{LEAGUES[league]}/{year:02}{year+1:02}_{league}.csv'
        url = f'https://raw.githubusercontent.com/sosthene14/footballdataset/{REV}/datasets/{path}'
        raw = urllib.request.urlopen(url, timeout=30).read()
        rows = parse_source(raw, league, year)
        return rows, dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), matches=len(rows),
                          missing_prices=sum(r['prices'] is None for r in rows))
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(one, [(lg, yr) for lg in LEAGUES for yr in range(19, 25)]))
    return sorted([r for rows, _ in parts for r in rows], key=lambda r: r['match_id']), [m for _, m in parts]


def split(rows):
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate match')
    if any(r['league'] not in LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected split member')
    train = [r for r in rows if r['season'] <= 22 and r['prices'] is not None]
    test = [r for r in rows if r['season'] >= 23 and r['prices'] is not None]
    if not train or not test or max(r['date'] for r in train) >= min(r['date'] for r in test):
        raise ValueError('Temporal split overlap/empty')
    return train, test


def probabilities(rows):
    return validate(np.array([shin(r['prices']) for r in rows]))


def validate(p):
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or (p <= 0).any() or not np.allclose(p.sum(1), 1):
        raise ValueError('Invalid probabilities')
    return p


def fit_draw(train):
    if not train or any(r['season'] > 22 for r in train):
        raise ValueError('Only past training seasons allowed')
    offsets = logit(probabilities(train)[:, 1])
    y = np.array([r['y'] == 1 for r in train])
    return float(brentq(lambda b: np.mean(expit(offsets+b)-y), -10, 10, xtol=1e-12))


def calibrate(p, bias):
    p = validate(p)
    qd = expit(logit(p[:, 1])+bias)
    q = p*((1-qd)/(1-p[:, 1]))[:, None]
    q[:, 1] = qd
    return validate(q)


def select(rows, p):
    """Receives pregame-only dictionaries. One DD/control ticket per league/date."""
    p = validate(p)
    if len(rows) != len(p) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('Selection length/duplicate')
    groups = defaultdict(list)
    for r, probs in zip(rows, p):
        odds = r['prices']
        if len(odds) != 3 or any(not np.isfinite(o) or o <= 1 for o in odds):
            raise ValueError('Invalid odds')
        options = [dict(id=r['match_id'], choice=i, odds=o, probability=float(probs[i]))
                   for i, o in enumerate(odds) if o > min(odds)]
        groups[(r['league'], r['date'])].append(options)
    tickets = {}
    for key, events in sorted(groups.items()):
        events.sort(key=lambda e: e[0]['id'] if e else '')
        candidates = []
        for first, second in combinations(events, 2):
            for a, b in product(first, second):
                score = a['probability']*a['odds']*b['probability']*b['odds']
                candidates.append((-score, a['id'], a['choice'], b['id'], b['choice'], [a, b]))
        tickets[key] = min(candidates, key=lambda t: t[:5])[-1] if candidates else []
    return tickets


def settle(all_test, tickets):
    actual = {r['match_id']: r for r in all_test}
    groups = sorted({(r['league'], r['date'], r['season']) for r in all_test})
    output = []
    for league, day, season in groups:
        pair = tickets.get((league, day), [])
        if pair and (len(pair) != 2 or len({a['id'] for a in pair}) != 2):
            raise ValueError('Invalid ticket')
        if any((actual[a['id']]['league'], actual[a['id']]['date']) != (league, day) for a in pair):
            raise ValueError('Cross-budget ticket')
        won = int(bool(pair) and all(actual[a['id']]['y'] == a['choice'] for a in pair))
        raw = float(np.prod([a['odds'] for a in pair])) if pair else 0.
        rounded = payout(*[a['odds'] for a in pair]) if pair else 0.
        stake = int(bool(pair))
        predicted = float(np.prod([a['probability'] for a in pair])) if pair else 0.
        output.append(dict(league=league, date=day, season=season, budget=1, stake=stake,
                           wins=won, raw=won*raw-stake, rounded=won*rounded-stake,
                           predicted=predicted, gap=won-predicted, draws=sum(a['choice'] == 1 for a in pair), pair=pair))
    return output


def interval(rows, numerator, denominator):
    weekly = defaultdict(lambda: np.zeros(2))
    for r in rows:
        key = tuple(datetime.fromisoformat(r['date']).isocalendar()[:2])
        weekly[key] += [r[numerator], r[denominator]]
    if not weekly:
        return None
    values = np.array([weekly[k] for k in sorted(weekly)])
    rng = np.random.default_rng(SEED)
    sample = values[rng.integers(len(values), size=(REPS, len(values)))].sum(1)
    usable = sample[:, 1] > 0
    return np.quantile(sample[usable, 0]/sample[usable, 1], [.025, .975]).tolist() if usable.any() else None


def ratio_summary(rows, numerator, denominator):
    n, d = sum(r[numerator] for r in rows), sum(r[denominator] for r in rows)
    return dict(value=n/d if d else None, ci95=interval(rows, numerator, denominator))


def ticket_summary(rows):
    n = sum(r['stake'] for r in rows)
    return dict(budgets=len(rows), tickets=n, wins=sum(r['wins'] for r in rows),
                coverage=n/len(rows) if rows else None, profit_raw=sum(r['raw'] for r in rows),
                profit_proto_round_scenario=sum(r['rounded'] for r in rows),
                roi=ratio_summary(rows, 'raw', 'stake'),
                roi_proto_round_scenario=ratio_summary(rows, 'rounded', 'stake'),
                budget_return=ratio_summary(rows, 'raw', 'budget'),
                hit_rate=ratio_summary(rows, 'wins', 'stake'),
                pair_actual_minus_predicted=ratio_summary(rows, 'gap', 'stake'))


def binary_loss(p, y):
    return -(y*np.log(p)+(1-y)*np.log1p(-p))


def probability_summary(rows, p, q):
    metrics = []
    for r, a, b in zip(rows, p, q):
        y = np.eye(3)[r['y']]
        item = dict(date=r['date'], n=1)
        weak = 0 if r['prices'][0] > r['prices'][2] else 2 if r['prices'][2] > r['prices'][0] else None
        item['weak_n'] = int(weak is not None)
        for name, v in [('base', a), ('draw', b)]:
            item[name+'_ll'] = float(-np.log(v[r['y']]))
            item[name+'_brier'] = float(np.square(v-y).sum())
            item[name+'_draw_ll'] = float(binary_loss(v[1], y[1]))
            item[name+'_draw_brier'] = float((v[1]-y[1])**2)
            item[name+'_draw_p'] = float(v[1])
            item[name+'_draw_y'] = float(y[1])
            item[name+'_draw_gap'] = float(y[1]-v[1])
            for label, value in [('ll', binary_loss(v[weak], y[weak]) if weak is not None else 0.),
                                 ('brier', (v[weak]-y[weak])**2 if weak is not None else 0.),
                                 ('p', v[weak] if weak is not None else 0.), ('y', y[weak] if weak is not None else 0.)]:
                item[name+'_weak_'+label] = float(value)
            item[name+'_weak_gap'] = item[name+'_weak_y']-item[name+'_weak_p']
        item['delta_draw_ll'] = item['draw_draw_ll']-item['base_draw_ll']
        metrics.append(item)
    output = dict(matches=len(rows), weak_matches=sum(r['weak_n'] for r in metrics),
                  delta_draw_logloss=ratio_summary(metrics, 'delta_draw_ll', 'n'))
    for name in ['base', 'draw']:
        output[name] = {field: ratio_summary(metrics, name+'_'+field, 'weak_n' if field.startswith('weak') else 'n')
                        for field in ['ll', 'brier', 'draw_ll', 'draw_brier', 'draw_p', 'draw_y', 'draw_gap', 'weak_ll', 'weak_brier', 'weak_p', 'weak_y', 'weak_gap']}
    return output


def paired_summary(a, b):
    if [(r['league'], r['date']) for r in a] != [(r['league'], r['date']) for r in b]:
        raise ValueError('Unpaired budgets')
    rows = [dict(date=x['date'], delta=y['raw']-x['raw'], rounded_delta=y['rounded']-x['rounded'], budget=1)
            for x, y in zip(a, b)]
    return dict(raw=ratio_summary(rows, 'delta', 'budget'),
                proto_round_scenario=ratio_summary(rows, 'rounded_delta', 'budget'),
                changed_tickets=sum([(v['id'], v['choice']) for v in x['pair']] != [(v['id'], v['choice']) for v in y['pair']] for x, y in zip(a, b)))


def run():
    # Refuse to inspect outcomes unless the frozen protocol is in Git history.
    frozen = subprocess.check_output(['git', 'show', 'cbb8ef02:'+PROTOCOL], cwd=ROOT)
    if frozen.replace(b'\r\n', b'\n') != (ROOT/PROTOCOL).read_bytes().replace(b'\r\n', b'\n'):
        raise ValueError('Protocol differs from frozen commit')
    raw, sources = download()
    train, test = split(raw)
    bias = fit_draw(train)
    p = probabilities(test)
    q = calibrate(p, bias)
    pregame = [{k: r[k] for k in ('match_id', 'league', 'date', 'prices')} for r in test]
    all_test = [r for r in raw if r['season'] >= 23]
    records = {name: settle(all_test, select(pregame, probs)) for name, probs in [('base', p), ('draw', q)]}
    groups = {'all': lambda r: True, **{lg: (lambda r, lg=lg: r['league'] == lg) for lg in LEAGUES},
              **{str(yr): (lambda r, yr=yr: r['season'] == yr) for yr in (23, 24)}}
    results = {}
    for label, condition in groups.items():
        indices = [i for i, r in enumerate(test) if condition(r)]
        rec = {name: [r for r in rs if condition(r)] for name, rs in records.items()}
        results[label] = dict(probability=probability_summary([test[i] for i in indices], p[indices], q[indices]),
                              policy={name: ticket_summary(rs) for name, rs in rec.items()},
                              paired=paired_summary(rec['base'], rec['draw']))
    draw_groups = {}
    for name, rs in records.items():
        draw_groups[name] = {}
        for d in range(3):
            masked = [{**r, **({} if r['stake'] and r['draws'] == d else
                       dict(stake=0, wins=0, raw=0., rounded=0., predicted=0., gap=0.))} for r in rs]
            draw_groups[name][str(d)] = ticket_summary(masked)
    return dict(protocol_commit='cbb8ef02', source_revision=REV, sources=sources,
                environment=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                dependency_sha256={path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                                   for path in ['src/devig.py', 'scripts/evaluate_external_temporal_roi.py']},
                split={name: dict(matches=len(rs), first=min(r['date'] for r in rs), last=max(r['date'] for r in rs))
                       for name, rs in [('train', train), ('test', test)]},
                bias=bias, bootstrap=dict(seed=SEED, repetitions=REPS, cluster='ISO year/week across leagues'),
                results=results, draw_groups=draw_groups, records=records,
                limitations=['Audited external replication; globally unused data not proven.',
                             'Market surrogate, not the production or PR254 conditional model.',
                             'B365 nonclosing prices lack timestamp and are not Proto purchase prices.',
                             'Rounded returns are only a hypothetical settlement scenario.',
                             'Pair probabilities assume independence; secondary intervals unadjusted.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(bias=report['bias'], split=report['split'], results=report['results']['all']), ensure_ascii=False))
