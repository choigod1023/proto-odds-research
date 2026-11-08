"""Retrospective KBL winner study; no network, wallet, or operational writes."""
from collections import Counter, defaultdict
from datetime import date
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import ndtr
from scipy.stats import t

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OFFICIAL = ROOT / 'experiments/pregame-basketball-20261104/season-records.jsonl'
PRICES = ROOT / 'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
ARTIFACT = ROOT / 'data/model_candidates/handicap-v1.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_games(records):
    fixtures, results = defaultdict(list), defaultdict(list)
    for row in records:
        if row.get('league') != 'KBL':
            continue
        payload = row.get('payload') or {}
        ident = payload.get('source_fixture_id')
        if not ident:
            continue
        if row.get('kind') == 'fixture':
            fixtures[ident].append(row)
        elif row.get('kind') == 'result':
            results[ident].append(row)
    games, reasons = [], Counter()
    for ident, fs in fixtures.items():
        rs = results.get(ident, [])
        if len(fs) != 1 or len(rs) != 1:
            reasons['missing_or_duplicate_fixture_result'] += 1
            continue
        f, r = fs[0], rs[0]
        if (f['kickoff'], f['home'], f['away']) != (r['kickoff'], r['home'], r['away']):
            reasons['fixture_result_identity_conflict'] += 1
            continue
        payload, score = f['payload'], r['payload']
        h, a = score.get('home_score'), score.get('away_score')
        if type(h) is not int or type(a) is not int or min(h, a) < 0 or h == a:
            reasons['invalid_or_tied_score'] += 1
            continue
        home, away = str(payload.get('home_team_id') or ''), str(payload.get('away_team_id') or '')
        if not home or not away or home == away:
            reasons['invalid_team_id'] += 1
            continue
        games.append({'day': f['kickoff'][:10], 'home': home, 'away': away, 'margin': h-a})
    return games, dict(reasons)


def fit(games):
    if len(games) < 2:
        raise ValueError('insufficient training')
    mean = sum(g['margin'] for g in games) / len(games)
    accum = defaultdict(lambda: [0., 0])
    for g in games:
        for team, sign in ((g['home'], 1), (g['away'], -1)):
            accum[team][0] += sign * (g['margin'] - mean)
            accum[team][1] += 1
    teams = {team: total / (n + 10) for team, (total, n) in accum.items()}
    errors = [g['margin'] - (mean + (teams[g['home']] - teams[g['away']]) / 2) for g in games]
    sd = math.sqrt(sum(v*v for v in errors) / len(errors))
    if sd <= 0:
        raise ValueError('invalid training variance')
    return {'n': len(games), 'mean': mean, 'sd': sd, 'teams': teams}


def margin_prob(model, home, away):
    if home not in model['teams'] or away not in model['teams']:
        return None
    mu = model['mean'] + (model['teams'][home] - model['teams'][away]) / 2
    return float(ndtr(mu / model['sd'])), float(t.cdf(mu / (model['sd'] * math.sqrt(3/5)), df=5))


def join(games, quotes, aliases):
    idx = defaultdict(list)
    for g in games:
        idx[(g['day'], g['home'], g['away'])].append(g)
    qidx = defaultdict(list)
    counts = Counter()
    for row in quotes:
        if row.get('sport') != 'bk' or row.get('league') != 'KBL' or row['day'][:4] not in ('2025', '2026'):
            continue
        counts['price_rows_'+row['day'][:4]] += 1
        home, away = (aliases.get(''.join(row.get(side, '').split())) for side in ('home', 'away'))
        if not home or not away or home == away:
            counts['missing_team_alias'] += 1
            continue
        qidx[(row['day'], home, away)].append(row)
    joined = []
    for key, qs in sorted(qidx.items()):
        gs = idx.get(key, [])
        if len(qs) != 1 or len(gs) != 1:
            counts['missing_or_ambiguous_fixture'] += 1
            continue
        q, g = qs[0], gs[0]
        odds = q.get('odds') or []
        if len(odds) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 1 for v in odds):
            counts['invalid_price'] += 1
            continue
        y = int(g['margin'] > 0)
        if q.get('y') != (0 if y else 1):
            counts['winner_label_conflict'] += 1
            continue
        counts['matched_'+g['day'][:4]] += 1
        joined.append(dict(g, y=y, odds=odds))
    return joined, dict(counts)


def metrics(rows, arm):
    n = len(rows)
    if not n:
        return None
    hits, prices, nets, ll, br = [], [], [], [], []
    for r in rows:
        p = min(1-1e-12, max(1e-12, r['prob'][arm]))
        pick = int(p >= .5)
        hit = int(pick == r['y'])
        price = r['odds'][0 if pick else 1]
        hits.append(hit); prices.append(price); nets.append(price-1 if hit else -1)
        ll.append(-math.log(p if r['y'] else 1-p)); br.append((p-r['y'])**2)
    daily = defaultdict(float)
    for r, net in zip(rows, nets):
        daily[r['day']] += net
    equity = peak = mdd = 0.
    for d in sorted(daily):
        equity += daily[d]; peak = max(peak, equity); mdd = max(mdd, peak-equity)
    return {'n': n, 'hits': sum(hits), 'accuracy': sum(hits)/n, 'mean_odds': sum(prices)/n,
            'conditional_roi': sum(nets)/n, 'brier': sum(br)/n, 'logloss': sum(ll)/n,
            'daily_mdd_units': mdd}


def bootstrap(rows, arm, ref, seed=20261108, reps=2000):
    dates = sorted({r['day'] for r in rows})
    grouped = {d: [r for r in rows if r['day'] == d] for d in dates}
    rng = np.random.default_rng(seed)
    values = defaultdict(list)
    for _ in range(reps):
        sample = [r for day in rng.choice(dates, size=len(dates), replace=True) for r in grouped[day]]
        a, b = metrics(sample, arm), metrics(sample, ref)
        for k in ('accuracy', 'conditional_roi', 'brier', 'logloss'):
            values[k].append(a[k]-b[k])
    return {k: [float(x) for x in np.percentile(v, [2.5, 97.5])] for k, v in values.items()}


def run():
    records = [json.loads(s) for s in OFFICIAL.read_text(encoding='utf-8').splitlines()]
    with gzip.open(PRICES, 'rt', encoding='utf-8') as f:
        quotes = [json.loads(s) for s in f]
    aliases = json.loads(ARTIFACT.read_text(encoding='utf-8'))['KBL']['aliases']
    games, exclusions = official_games(records)
    joined, counts = join(games, quotes, aliases)
    periods = [('validation', '2025-09-26', '2025-10-01', '2025-12-31'),
               ('evaluation', '2025-12-27', '2026-01-01', '2026-12-31')]
    summary = {'source_sha256': {p.name: sha(p) for p in (OFFICIAL, PRICES, ARTIFACT)},
               'official_games': Counter(g['day'][:4] for g in games), 'official_exclusions': exclusions,
               'join': counts, 'periods': {}}
    all_rows = []
    for name, cutoff, first, last in periods:
        train = [g for g in games if '2025-01-01' <= g['day'] <= cutoff]
        model = fit(train)
        opportunities = [g for g in joined if first <= g['day'] <= last]
        rows = []
        for g in opportunities:
            probs = margin_prob(model, g['home'], g['away'])
            if probs is None:
                continue
            market = (1/g['odds'][0]) / (1/g['odds'][0] + 1/g['odds'][1])
            ident = hashlib.sha256((g['day']+'|'+g['home']+'|'+g['away']).encode()).hexdigest()[:20]
            rows.append({'id': ident, 'day': g['day'], 'y': g['y'], 'odds': g['odds'],
                         'prob': {'market': market, 'normal': probs[0], 'student_t5': probs[1]}, 'period': name})
        arms = ('market', 'normal', 'student_t5')
        groups = {arm: metrics(rows, arm) for arm in arms}
        months = {month: {arm: metrics([r for r in rows if r['day'][:7] == month], arm) for arm in arms}
                  for month in sorted({r['day'][:7] for r in rows})}
        ci = {arm+'_minus_'+ref: bootstrap(rows, arm, ref) for arm, ref in
              (('normal','market'), ('student_t5','normal'), ('student_t5','market'))} if rows else {}
        summary['periods'][name] = {'training_until': cutoff, 'training_n': model['n'],
            'mean_margin': model['mean'], 'residual_sd': model['sd'], 'opportunities': len(opportunities),
            'covered': len(rows), 'coverage': len(rows)/len(opportunities) if opportunities else None,
            'missing_training_team': len(opportunities)-len(rows), 'metrics': groups, 'month': months, 'paired_day_bootstrap_95': ci}
        all_rows.extend(rows)
    with (HERE/'predictions.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as out:
            for r in all_rows:
                out.write((json.dumps(r, sort_keys=True, separators=(',', ':'))+'\n').encode())
    (HERE/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    run()
