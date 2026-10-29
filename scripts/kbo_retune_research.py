"""Offline KBO research. All executable inputs live in the committed experiment directory."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import expit, logit
from scipy.stats import nbinom

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'experiments/baseball-league-retune-20261029/kbo'
TEAMS = {'LG', 'KT', '삼성', '한화', 'SSG', '키움', '두산', 'NC', 'KIA', '롯데'}
STRENGTH = ['season_strength', 'recent_strength', 'venue_net', 'rest', 'games7']
BATTING = ['season_ops_proxy', 'recent_ops_proxy']
BULLPEN = ['starter_innings', 'relief_kbb', 'dependency_load']
BUNDLES = {'strength': STRENGTH, 'batting': STRENGTH+BATTING,
           'bullpen': STRENGTH+BULLPEN, 'full': STRENGTH+BATTING+BULLPEN}


def read(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf8') as f:
        return [json.loads(line) for line in f if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                    encoding='utf8', newline='\n')


def write_rows(path, rows):
    payload = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rows).encode()
    if path.suffix == '.gz':
        path.write_bytes(gzip.compress(payload, mtime=0))
    else:
        path.write_bytes(payload)


def key(r):
    return r['date'], r['home'], r['away']


def prices_order(r):
    return (r['prices'], r['order']) if 'prices' in r else (r['historical_prices'], ['home', 'away'])


def market(r):
    """Independent copy of frozen main devig.shin, including its fallback checks."""
    prices, order = prices_order(r)
    if len(prices) != 2 or sorted(order) != ['away', 'home']:
        raise ValueError('two_way_price_order_required')
    if any(not math.isfinite(float(p)) or float(p) <= 1 for p in prices):
        raise ValueError('invalid_prices')
    pi = [1/float(p) for p in prices]
    total = sum(pi)
    fallback = [p/total for p in pi]
    if total <= 1:
        return fallback[order.index('home')]

    def probs(z):
        if z <= 1e-12:
            return fallback
        return [(math.sqrt(z*z+4*(1-z)*v*v/total)-z)/(2*(1-z)) for v in pi]

    lo, hi = 0., .9
    for _ in range(200):
        mid = (lo+hi)/2
        s = sum(probs(mid))
        if abs(s-1) < 1e-12:
            break
        if s > 1:
            lo = mid
        else:
            hi = mid
    out = probs((lo+hi)/2)
    out = [p/sum(out) for p in out]
    if any(not 0 < p < 1 for p in out):
        out = fallback
    return out[order.index('home')]


def reference(r):
    m = market(r)
    if r['role'] != 'evaluation':
        return m
    if r['voters'] <= 0 or sum(r['vote_counts'].values()) != r['voters']:
        raise ValueError('invalid_frozen_votes')
    return .9*m+.1*r['vote_counts'].get('home', 0)/r['voters']


def prepare(folder, source_root):
    """Only import; scoring is deliberately a separate command."""
    folder.mkdir(parents=True, exist_ok=True)
    sources = folder/'sources'
    sources.mkdir(exist_ok=True)
    parent = source_root/'sportstoto-worktrees'
    old = parent/'baseball-bullpen-external-periods-20261029/experiments/baseball-bullpen-external-periods-20261029'
    paths = {
        'pitching': old/'sources/asian-normalized-history.jsonl.gz',
        'cohort': old/'asian-cohort-inputs.jsonl',
        'scores': parent/'baseball-league-fit-20261029/experiments/baseball-league-fit-20261029/history.jsonl',
        'batters': source_root/'sportstoto/data/raw/detail/kbo_batters_indiv_2023_2026.json',
    }
    manifest = {'original_inputs': {n: {'path': str(p), 'sha256': digest(p)} for n, p in paths.items()},
                'policy': 'KBO-only lossless row extracts; batter source raw entries preserved; no runtime sibling dependencies'}
    for name in ['pitching', 'cohort', 'scores']:
        write_rows(sources/(name+'.jsonl.gz'), [r for r in read(paths[name]) if r['league'] == 'KBO'])
    raw = json.loads(paths['batters'].read_text(encoding='utf8'))
    write_rows(sources/'batters.jsonl.gz', [{'source_key': k, 'raw': v} for k, v in raw.items()])
    manifest['committed_inputs'] = {p.name: digest(p) for p in sorted(sources.glob('*.gz'))}
    write_json(folder/'input-manifest.json', manifest)
    print(json.dumps({'imported': manifest['committed_inputs']}, ensure_ascii=False))


def load_facts(folder):
    """Validate sources without using target outcomes to select a subset."""
    source = folder/'sources'
    manifest = json.loads((folder/'input-manifest.json').read_text(encoding='utf8'))
    for name, expected in manifest['committed_inputs'].items():
        if digest(source/name) != expected:
            raise ValueError('input_hash_changed:'+name)
    pitching, scores, cohorts, raw_batters = [read(source/(name+'.jsonl.gz'))
                                            for name in ['pitching', 'scores', 'cohort', 'batters']]
    raw = {b['source_key']: b['raw'] for b in raw_batters}
    fixtures = defaultdict(list)
    ids = set()
    for r in pitching:
        if r['official_id'] in ids:
            raise ValueError('duplicate_pitching_id')
        ids.add(r['official_id'])
        gid = r['official_id']
        # Naver postseason/all-star ids use synthetic year prefixes 3333..9999.
        if not (gid[4:8] == r['date'][5:].replace('-', '') and
                (gid[:4] == r['date'][:4] or (gid[:4] in {'3333', '4444', '5555', '6666', '7777', '9999'}
                                                           and gid[-4:] == r['date'][:4]))):
            raise ValueError('pitching_date_id_mismatch')
        for i, ps in enumerate(r['pitchers']):
            if not ps or any(not p['player'].startswith('kbo:') or
                             any(not math.isfinite(p[k]) or p[k] < 0 for k in ['outs', 'runs', 'k', 'bb'])
                             or p['outs'] != int(p['outs']) for p in ps):
                raise ValueError('invalid_pitching')
            if sum(p['runs'] for p in ps) != r['ag' if i == 0 else 'hg']:
                raise ValueError('pitching_run_sum_mismatch')
        fixtures[key(r)].append(r)
    # Each score row is a date/team anchor; pitching ids disambiguate doubleheaders.
    score_by_key = {}
    for r in scores:
        k = key(r)
        if k in score_by_key and (r['hg'], r['ag']) != (score_by_key[k]['hg'], score_by_key[k]['ag']):
            raise ValueError('conflicting_score_anchor')
        score_by_key[k] = r
        if fixtures.get(k) and not any((q['hg'], q['ag']) == (r['hg'], r['ag']) for q in fixtures[k]):
            raise ValueError('score_pitching_conflict')
    games = [dict(r, source_fixture_id=r['official_id']) for r in pitching]
    for k, r in score_by_key.items():
        if k not in fixtures:
            games.append({n: r[n] for n in ['league', 'date', 'home', 'away', 'hg', 'ag']} |
                         {'source_fixture_id': 'score:'+':'.join(k)})
    batting = []
    for r in pitching:
        b = raw.get(r['official_id'])
        if b is None:
            raise ValueError('missing_batter_source')
        if b['date'] != r['date'] or (b['home_score'], b['away_score']) != (r['hg'], r['ag']) or (b['home'], b['away']) != (r['home'], r['away']):
            raise ValueError('batter_score_or_date_mismatch')
        # Teams are determined from score-validated source gameId, not mojibake names.
        totals = []
        for side in ['home', 'away']:
            ps = b['data'][side]
            for p in ps:
                vals = [float(p.get(n) or 0) for n in ['ab', 'hit', 'bb', 'hr', 'd', 't']]
                ab, hit, bb, hr, doubles, triples = vals
                if not p.get('code') or any(not math.isfinite(v) or v < 0 or v != int(v) for v in vals):
                    raise ValueError('invalid_batter_count')
                if hit > ab or doubles+triples+hr > hit:
                    raise ValueError('invalid_hit_components')
            totals.append({n: sum(float(p.get(n) or 0) for p in ps) for n in ['ab', 'hit', 'bb', 'hr', 'd', 't']})
        batting.append({n: r[n] for n in ['date', 'home', 'away']} |
                       {'source_fixture_id': r['official_id'], 'totals': totals})
    # Same guard as #410: all known official score games within 28 days must have pitching.
    for r in cohorts:
        day = date.fromisoformat(r['date'])
        missing = [key(q) for q in score_by_key.values()
                   if 0 < (day-date.fromisoformat(q['date'])).days <= 28
                   and {r['home'], r['away']} & {q['home'], q['away']} and key(q) not in fixtures]
        if missing:
            raise ValueError('prior28_pitching_gap:'+str(key(r))+':'+str(missing[:2]))
        matches = fixtures.get(key(r), [])
        if len(matches) > 1 or (matches and (matches[0]['hg'], matches[0]['ag']) != (r['hg'], r['ag'])):
            raise ValueError('target_score_or_fixture_conflict')
        if r['role'] != 'evaluation' and len(matches) != 1:
            raise ValueError('historical_target_missing_box')
        prices_order(r)
        market(r)
    count = Counter(r['role'] for r in cohorts)
    if count != {'train': 710, 'validation': 712, 'evaluation': 198}:
        raise ValueError('fixed_cohort_changed:'+str(count))
    cohorts.sort(key=lambda r: (r['date'], r.get('fixture_id', ''), r['home'], r['away']))
    non_club = [r for r in games if {r['home'], r['away']}-TEAMS]
    games = [r for r in games if not ({r['home'], r['away']}-TEAMS)]
    audit = {'cohort_counts': dict(count), 'pitching_games': len(pitching), 'score_anchors': len(scores),
             'merged_score_games': len(games), 'batter_games_used': len(batting),
             'raw_batter_games': len(raw), 'doubleheader_extra_games': len(pitching)-len(fixtures),
             'non_club_allstar_games_not_in_league_environment': len(non_club),
             'naver_synthetic_year_ids': sum(r['official_id'][:4] != r['date'][:4] for r in pitching),
             'pitching_by_year': dict(Counter(r['date'][:4] for r in pitching)),
             'score_by_year': dict(Counter(r['date'][:4] for r in games)),
             'excluded_original_eval': 5, 'original_eval': 203,
             'source_guard': 'all fixed cohort rows passed, missing pitching is never rest zero',
             'team_mapping': 'raw batter gameId -> normalized pitching id, with date and scores checked'}
    return cohorts, games, pitching, batting, audit


def build_features(target, games, pitching, batting):
    """No read of current outcomes, prices, votes, lineup, or pitchers."""
    day = date.fromisoformat(target['date'])
    prior = [r for r in games if r['date'] < target['date'] and r['date'][:4] == target['date'][:4]]
    pp = [r for r in pitching if 0 < (day-date.fromisoformat(r['date'])).days <= 28]
    bp = [r for r in batting if r['date'] < target['date'] and r['date'][:4] == target['date'][:4]]
    L = (sum(r['hg']+r['ag'] for r in prior)+20*4.5)/(2*len(prior)+20)
    summaries, proofs = [], []
    for side, team in [('home', target['home']), ('away', target['away'])]:
        team_games = [r for r in prior if team in (r['home'], r['away'])]
        recent = [r for r in team_games if (day-date.fromisoformat(r['date'])).days <= 28]
        venue = [r for r in team_games if r[side] == team]
        sums = lambda rs, attack: sum(r['hg' if (r['home'] == team) == attack else 'ag'] for r in rs)
        value = {'n': len(team_games), 'runs': sums(team_games, True), 'allowed': sums(team_games, False),
                 'n28': len(recent), 'runs28': sums(recent, True), 'allowed28': sums(recent, False),
                 'venue_net': (sums(venue, True)-sums(venue, False))/(len(venue)+20)/L,
                 'rest': min(7, (day-max(date.fromisoformat(r['date']) for r in team_games)).days-1) if team_games else 7,
                 'games7': sum((day-date.fromisoformat(r['date'])).days <= 7 for r in team_games)}
        team_pp = [(r, r['pitchers'][0 if r['home'] == team else 1]) for r in pp if team in (r['home'], r['away'])]
        starter_outs = (sum(ps[0]['outs'] for _, ps in team_pp)+5*15)/(len(team_pp)+5)
        relief = [(r, p) for r, ps in team_pp for p in ps[1:]]
        outs = sum(p['outs'] for _, p in relief)
        value['starter_innings'] = starter_outs/3
        value['relief_kbb'] = (sum(p['k']-p['bb'] for _, p in relief)+45*.20)/(outs+45)
        load = sum(p['outs'] for r, p in relief if (day-date.fromisoformat(r['date'])).days <= 3)/27
        value['dependency_load'] = float(np.clip(1-starter_outs/27, 0, 1))*load
        team_b = [(r, r['totals'][0 if r['home'] == team else 1]) for r in bp if team in (r['home'], r['away'])]
        for field, subset in [('season_ops_proxy', team_b), ('recent_ops_proxy', [(r, b) for r, b in team_b if
                                            (day-date.fromisoformat(r['date'])).days <= 28])]:
            totals = {n: sum(b[n] for _, b in subset) for n in ['ab', 'hit', 'bb', 'hr', 'd', 't']}
            v = totals
            value[field] = ((v['hit']+v['bb']+100*.32)/(v['ab']+v['bb']+100) +
                            (v['hit']+v['d']+2*v['t']+3*v['hr']+100*.40)/(v['ab']+100))
        summaries.append(value)
        proofs.append({'team': team, 'score_games': len(team_games), 'score_games28': len(recent),
                       'pitching_games28': len(team_pp), 'batting_games': len(team_b),
                       'latest_score_date': max((r['date'] for r in team_games), default=None),
                       'score_ids': sorted(r['source_fixture_id'] for r in team_games),
                       'pitching_ids28': sorted(r['official_id'] for r, _ in team_pp),
                       'batter_ids': sorted(r['source_fixture_id'] for r, _ in team_b)})
    a, b = summaries

    def strength(s, window):
        suffix = '28' if window == '28days' else ''
        return math.log((s['runs'+suffix]+20*L)/(s['allowed'+suffix]+20*L))

    features = {n: a[n]-b[n] for n in ['venue_net', 'rest', 'games7']+BATTING+BULLPEN}
    features['season_strength'] = strength(a, 'season')-strength(b, 'season')
    features['recent_strength'] = strength(a, '28days')-strength(b, '28days')
    return {'features': features, 'league_runs': L, 'sides': summaries,
            'proof': {'source_date_rule': 'strictly earlier calendar date, same-season scores/batting',
                      'league_score_games': len(prior), 'home': proofs[0], 'away': proofs[1]}}


def candidate_specs():
    specs = {'baseline': {'kind': 'baseline'}, 'market_null': {'kind': 'market_null'}}
    for bundle, names in BUNDLES.items():
        for ridge in [.01, .1, 1.]:
            specs[f'logistic:{bundle}:ridge{ridge:g}'] = {'kind': 'logistic', 'names': names, 'ridge': ridge}
    for s in [20, 60]:
        for w in [.1, .3]:
            for window in ['season', '28days']:
                specs[f'nb:s{s}:{window}:w{w:g}'] = {'kind': 'nb', 'shrink': s, 'window': window, 'blend': w}
            for exponent in [1.5, 2.]:
                specs[f'pyth:s{s}:e{exponent:g}:w{w:g}'] = {'kind': 'pyth', 'shrink': s,
                                                                       'exponent': exponent, 'window': 'season', 'blend': w}
    assert len(specs) == 30
    return specs


def fit(rows, spec):
    spec = dict(spec)
    if any(r['date'][:4] not in {'2023', '2024'} for r in rows):
        raise ValueError('future_outcomes_in_training')
    spec['train_rows'] = len(rows)
    spec['train_non_draw'] = sum(r['hg'] != r['ag'] for r in rows)
    spec['train_first'], spec['train_last'] = min(r['date'] for r in rows), max(r['date'] for r in rows)
    if spec['kind'] in {'baseline', 'market_null'}:
        return spec
    if spec['kind'] == 'logistic':
        rr = [r for r in rows if r['hg'] != r['ag']]
        x = np.array([[r['features'][n] for n in spec['names']] for r in rr])
        scale = np.where(x.std(axis=0) > 1e-8, x.std(axis=0), 1.)
        x = np.clip(x/scale, -6, 6)
        y = np.array([r['hg'] > r['ag'] for r in rr], float)
        offset = logit([market(r) for r in rr])

        def objective(beta):
            eta = offset+x@beta
            return (float(np.mean(np.logaddexp(0, eta)-y*eta)+spec['ridge']*(beta@beta)/2),
                    x.T@(expit(eta)-y)/len(y)+spec['ridge']*beta)

        result = minimize(objective, np.zeros(x.shape[1]), jac=True, method='L-BFGS-B',
                          options={'maxiter': 2000, 'gtol': 1e-8, 'ftol': 1e-12})
        if not result.success:
            raise ValueError('fit_failed:'+str(result.message))
        spec.update(scale=scale.tolist(), beta=result.x.tolist(), optimizer=str(result.message))
    else:
        values = np.array([r[k] for r in rows for k in ['hg', 'ag']], float)
        mean, variance = float(values.mean()), float(values.var())
        spec.update(home_factor=.5*math.log((sum(r['hg'] for r in rows)+20*4.5)/
                                           (sum(r['ag'] for r in rows)+20*4.5)),
                    dispersion_r=float(np.clip(mean*mean/max(variance-mean, 1e-6), .5, 100)),
                    score_mean=mean, score_variance=variance)
    return spec


def run_means(row, config):
    L = row['league_runs']
    s = config['shrink']
    suffix = '28' if config['window'] == '28days' else ''
    values = [((a['runs'+suffix]+s*L)/(a['n'+suffix]+s),
               (a['allowed'+suffix]+s*L)/(a['n'+suffix]+s)) for a in row['sides']]
    h, a = values
    return h[0]*a[1]/L*math.exp(config['home_factor']), a[0]*h[1]/L*math.exp(-config['home_factor'])


def nb_probability(mu_h, mu_a, r):
    k = np.arange(201)
    h, a = [nbinom.pmf(k, r, r/(r+mu)) for mu in [mu_h, mu_a]]
    tail = max(float(1-h.sum()), float(1-a.sum()))
    if tail > 1e-8:
        raise ValueError('negative_binomial_tail_too_large')
    home = float(np.dot(h, np.r_[0., np.cumsum(a)[:-1]]))
    away = float(np.dot(a, np.r_[0., np.cumsum(h)[:-1]]))
    draw = float(h@a)
    return home/(home+away), draw, tail


def predict(config, rows):
    base = np.array([reference(r) for r in rows])
    if config['kind'] == 'baseline':
        return base
    if config['kind'] == 'market_null':
        return np.array([market(r) for r in rows])
    if config['kind'] == 'logistic':
        x = np.array([[r['features'][n] for n in config['names']] for r in rows])
        return expit(logit(base)+np.clip(x/np.array(config['scale']), -6, 6)@np.array(config['beta']))
    model = []
    for row in rows:
        h, a = run_means(row, config)
        p = nb_probability(h, a, config['dispersion_r'])[0] if config['kind'] == 'nb' else expit(config['exponent']*math.log(h/a))
        model.append(p)
    return expit((1-config['blend'])*logit(base)+config['blend']*logit(np.clip(model, 1e-10, 1-1e-10)))


def settlement(row, p):
    if row['hg'] == row['ag']:
        return 0, 0, 0
    side = 'home' if p >= .5 else 'away'
    hit = (side == 'home') == (row['hg'] > row['ag'])
    prices, order = prices_order(row)
    profit = (Decimal(str(prices[order.index(side)]))-1)*10000 if hit else Decimal(-10000)
    if profit != int(profit):
        raise ValueError('non_integer_krw_price')
    return int(hit), int(profit), 1


def metrics(rows, probabilities):
    if len(rows) != len(probabilities) or not len(rows):
        raise ValueError('empty_or_misaligned_metrics')
    daily, monthly = defaultdict(int), defaultdict(int)
    hits = net = settled = 0
    ps, ys = [], []
    for row, p in zip(rows, probabilities):
        if not math.isfinite(p) or not 0 < p < 1:
            raise ValueError('invalid_probability')
        h, profit, n = settlement(row, p)
        hits += h
        net += profit
        settled += n
        daily[row['date']] += profit
        monthly[row['date'][:7]] += profit
        if n:
            ps.append(p)
            ys.append(float(row['hg'] > row['ag']))
    p, y = np.array(ps), np.array(ys)
    balance = peak = drawdown = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
    return {'n': len(rows), 'bets': len(rows), 'coverage': 1., 'wins': hits, 'settled': settled,
            'pushes': len(rows)-settled, 'accuracy': hits/settled, 'net_krw': net,
            'roi': net/(len(rows)*10000), 'logloss': float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))),
            'brier': float(np.mean((p-y)**2)), 'monthly_net_krw': dict(sorted(monthly.items())),
            'daily_close_max_drawdown_krw': drawdown}


def choose(candidates):
    base = candidates['baseline']['validation']
    eligible = [n for n, c in candidates.items() if c['validation']['accuracy'] >= base['accuracy']
                and c['validation']['net_krw'] >= base['net_krw']]
    m = lambda n: candidates[n]['validation']
    return {'primary': min(eligible, key=lambda n: (m(n)['logloss'], m(n)['brier'], n)),
            'hit_leader': min(candidates, key=lambda n: (-m(n)['accuracy'], m(n)['brier'], n)),
            'profit_leader': min(candidates, key=lambda n: (-m(n)['net_krw'], m(n)['logloss'], n)),
            'eligible': sorted(eligible)}


def paired_interval(rows, p, baseline):
    groups = defaultdict(lambda: np.zeros(4))
    for row, a, b in zip(rows, p, baseline):
        d = date.fromisoformat(row['date'])
        week = (d-timedelta(days=d.weekday())).isoformat()
        ah, ap, n = settlement(row, a)
        bh, bp, _ = settlement(row, b)
        groups[week] += [ah-bh, (ap-bp)/10000, n, 1]
    values = np.array([groups[w] for w in sorted(groups)])
    rng = np.random.default_rng(20261029)
    sums = values[rng.integers(len(values), size=(5000, len(values)))].sum(axis=1)
    valid = sums[:, 2] > 0
    return {'weeks': len(values), 'replicates': 5000, 'seed': 20261029,
            'accuracy_delta95': np.quantile(sums[valid, 0]/sums[valid, 2], [.025, .975]).tolist(),
            'roi_delta95': np.quantile(sums[:, 1]/sums[:, 3], [.025, .975]).tolist()}


def ledger_row(row):
    prices, order = prices_order(row)
    return {k: row[k] for k in ['league', 'date', 'home', 'away', 'hg', 'ag', 'role']} | {
        'fixture_id': row.get('fixture_id') or row['source_fixture_id'],
        'source_fixture_id': row.get('source_fixture_id'), 'prices': prices, 'order': order,
        'baseline_kind': 'market90_vote10_frozen_research' if row['role'] == 'evaluation' else 'market_only_no_historical_votes',
        'baseline_probability': reference(row), 'market_probability': market(row)}


def input_hashes(folder):
    files = [folder/'PROTOCOL.md', folder/'input-manifest.json', *sorted((folder/'sources').glob('*.gz'))]
    # Text hashes normalize Git's Windows checkout newline conversion.
    return {str(p.relative_to(folder)).replace('\\', '/'): (
        hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() if p.suffix != '.gz' else digest(p)) for p in files}


def materialize_features(folder):
    cohorts, games, pitching, batting, audit = load_facts(folder)
    fixtures = defaultdict(list)
    for g in pitching:
        fixtures[key(g)].append(g)
    out = []
    for row in cohorts:
        feature = build_features(row, games, pitching, batting)
        ids = fixtures.get(key(row), [])
        out.append(dict(row, **feature, source_fixture_id=ids[0]['official_id'] if len(ids) == 1 else
                        'cohort:'+row.get('fixture_id', ':'.join(key(row)))))
    return out, audit


def freeze(folder):
    if (folder/'frozen-selection.json').exists():
        raise ValueError('selection_already_frozen; use committed frozen facts for evaluate')
    rows, audit = materialize_features(folder)
    train = [r for r in rows if r['role'] == 'train']
    val = [r for r in rows if r['role'] == 'validation']
    assert max(r['date'] for r in train) < min(r['date'] for r in val)
    candidates, vpred = {}, {}
    for name, spec in candidate_specs().items():
        first = fit(train, spec)
        ps = predict(first, val)
        vpred[name] = ps.tolist()
        candidates[name] = {'specification': spec, 'selection_fit': first, 'validation': metrics(val, ps),
                            'validation_changes_vs_market': int(np.sum((ps >= .5) != (np.array([market(r) for r in val]) >= .5))),
                            'final_fit': fit(train+val, spec)}
    selected = choose(candidates)
    frozen = {'protocol_hash': input_hashes(folder)['PROTOCOL.md'], 'input_sha256': input_hashes(folder),
              'feature_hash_train_validation': canonical_hash([r for r in rows if r['role'] != 'evaluation']),
              'selection_rule': 'eligible hit>=market and ROI>=market; minimum logloss/Brier/name; baseline included',
              'heads': selected, 'candidates': candidates, 'source_audit': audit,
              'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__}}
    write_json(folder/'frozen-selection.json', frozen)
    write_rows(folder/'validation_predictions.jsonl', [ledger_row(r) | {
        'candidate_probabilities': {n: p[i] for n, p in vpred.items()}} for i, r in enumerate(val)])
    write_rows(folder/'features.jsonl.gz', rows)
    write_json(folder/'source-audit.json', audit)
    print(json.dumps({'heads': selected, 'validation_baseline': candidates['baseline']['validation'],
                      'freeze_hash': digest(folder/'frozen-selection.json')}, ensure_ascii=False))


def evaluate(folder):
    frozen = json.loads((folder/'frozen-selection.json').read_text(encoding='utf8'))
    if input_hashes(folder) != frozen['input_sha256']:
        raise ValueError('frozen_input_or_protocol_changed')
    rows, audit = materialize_features(folder)
    if canonical_hash([r for r in rows if r['role'] != 'evaluation']) != frozen['feature_hash_train_validation']:
        raise ValueError('training_features_changed')
    ev = [r for r in rows if r['role'] == 'evaluation']
    if [sum(r['date'] < '2026-07-01' for r in ev), sum(r['date'] >= '2026-07-01' for r in ev)] != [152, 46]:
        raise ValueError('period_counts_changed')
    candidates = frozen['candidates']
    probabilities = {n: predict(c['final_fit'], ev) for n, c in candidates.items()}
    baseline = probabilities['baseline']
    for name, c in candidates.items():
        c['evaluation'] = metrics(ev, probabilities[name])
        c['evaluation_changes_vs_baseline'] = int(np.sum((probabilities[name] >= .5) != (baseline >= .5)))
    periods = {}
    for period, indexes in {'all': list(range(len(ev))),
                             'before_july': [i for i, r in enumerate(ev) if r['date'] < '2026-07-01'],
                             'july_onward': [i for i, r in enumerate(ev) if r['date'] >= '2026-07-01']}.items():
        rr = [ev[i] for i in indexes]
        bb = baseline[indexes]
        periods[period] = {'baseline': metrics(rr, bb), 'heads': {}}
        for role in ['primary', 'hit_leader', 'profit_leader']:
            name = frozen['heads'][role]
            pp = probabilities[name][indexes]
            periods[period]['heads'][role] = {'name': name, 'metrics': metrics(rr, pp),
                'changes_vs_baseline': int(np.sum((pp >= .5) != (bb >= .5))),
                'paired_week_bootstrap': paired_interval(rr, pp, bb)}
    ledger = []
    for i, row in enumerate(ev):
        r = ledger_row(row)
        r['frozen_votes'] = {'voters': row['voters'], 'vote_counts': row['vote_counts']}
        r['candidate_probabilities'] = {n: float(p[i]) for n, p in probabilities.items()}
        for role in ['primary', 'hit_leader', 'profit_leader']:
            name = frozen['heads'][role]
            r[role] = {'candidate': name, 'probability': float(probabilities[name][i])}
        ledger.append(r)
    result = {'league': 'KBO', 'candidate_count': len(candidates), 'concrete_research_candidates': 28,
              'heads': frozen['heads'], 'selection_rule': frozen['selection_rule'],
              'candidates': candidates, 'periods': periods, 'input_sha256': input_hashes(folder),
              'frozen_selection_sha256': digest(folder/'frozen-selection.json'),
              'executable_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
              'source_audit': audit, 'versions': frozen['versions'],
              'limitations': ['previously seen retrospective evaluation set', 'calendar-date causal, purchase-time unverified',
                             'validation has market only; evaluation uses frozen research market90/vote10 reference',
                             'no current announced starters or lineup', 'OPS proxy omits HBP/SF',
                             'NB independent final-score approximation, not inning/extra-inning simulation',
                             'multiple candidate selection, no multiplicity correction; not proof of future profit']}
    write_json(folder/'results.json', result)
    write_rows(folder/'predictions.jsonl', ledger)
    print(json.dumps({'heads': frozen['heads'], 'periods': periods}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'freeze', 'evaluate'])
    parser.add_argument('--folder', type=Path, default=DEFAULT)
    parser.add_argument('--source-root', type=Path, default=Path('C:/Users/user/Documents/ChatGPT'))
    args = parser.parse_args()
    if args.phase == 'prepare':
        prepare(args.folder, args.source_root)
    elif args.phase == 'freeze':
        freeze(args.folder)
    else:
        evaluate(args.folder)
