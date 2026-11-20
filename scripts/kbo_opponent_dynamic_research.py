"""Offline opponent-adjusted states. Common I/O/Shin/settlement adapted from prior KBO research."""
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
import shutil
import sys
import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import expit, logit
ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'experiments/baseball-information-stepchange-20261029/kbo'
TEAMS = sorted(['LG', 'KT', '삼성', '한화', 'SSG', '키움', '두산', 'NC', 'KIA', '롯데'])


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


def state_specs():
    result = {}
    for i, (k, half, keep) in enumerate([(.08, 120, .35), (.16, 90, .5), (.28, 60, .65)]):
        result['e'+str(i)] = dict(family='elo', k=k, half_life=half, season_keep=keep)
    for i, (process, half, keep) in enumerate([(.0005, 180, .35), (.002, 120, .5), (.006, 60, .65)]):
        result['a'+str(i)] = dict(family='attack_defence', process=process,
                                 half_life=half, season_keep=keep)
    return result


def candidate_specs():
    result = {'baseline': {'kind': 'baseline'}, 'market_null': {'kind': 'market_null'}}
    for state in state_specs():
        for ridge in [.03, .30]:
            result[f'{state}:residual:{ridge}'] = dict(kind='residual', state=state, ridge=ridge)
        for weight in [.15, .35]:
            result[f'{state}:blend:{weight}'] = dict(kind='blend', state=state, weight=weight)
    assert len(result) == 26
    return result


def normalized_hash(path):
    data = path.read_bytes()
    if path.suffix != '.gz':
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def input_hashes(folder):
    files = [folder/'PROTOCOL.md', folder/'input-manifest.json',
             *sorted((folder/'sources').glob('*')), Path(__file__)]
    return {('code/'+p.name if p == Path(__file__) else p.relative_to(folder).as_posix()):
            normalized_hash(p) for p in files}


def prepare(folder, source):
    """Import once, without fitting or scoring any candidate."""
    (folder/'sources').mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in ['cohort', 'pitching', 'scores']:
        p = source/'sources'/(name+'.jsonl.gz')
        dest = folder/'sources'/p.name
        shutil.copyfile(p, dest)
        manifest[dest.relative_to(folder).as_posix()] = {'origin': str(p), 'sha256': digest(p)}
    for name in ['source-audit.json', 'input-manifest.json', 'PROTOCOL.md',
                 'predictions.jsonl', 'validation_predictions.jsonl', 'results.json']:
        p = source/name
        dest = folder/'sources'/('prior-'+name)
        shutil.copyfile(p, dest)
        manifest[dest.relative_to(folder).as_posix()] = {'origin': str(p), 'sha256': normalized_hash(p)}
    write_json(folder/'input-manifest.json', manifest)
    print(json.dumps({'imported_files': len(manifest)}))


def load_facts(folder):
    manifest = json.loads((folder/'input-manifest.json').read_text(encoding='utf8'))
    for name, item in manifest.items():
        if normalized_hash(folder/name) != item['sha256']:
            raise ValueError('source_hash_changed:'+name)
    cohorts, pitching, scores = [read(folder/'sources'/(n+'.jsonl.gz'))
                                 for n in ['cohort', 'pitching', 'scores']]
    fixtures, anchors, ids = defaultdict(list), {}, set()
    for r in pitching:
        gid = r['official_id']
        if gid in ids:
            raise ValueError('duplicate_game_id')
        ids.add(gid)
        if not (gid[4:8] == r['date'][5:].replace('-', '') and
                (gid[:4] == r['date'][:4] or (gid[:4] in {'3333', '4444', '5555', '6666', '7777', '9999'}
                                                          and gid[-4:] == r['date'][:4]))):
            raise ValueError('date_game_id_mismatch')
        for i, ps in enumerate(r['pitchers']):
            if not ps or any(not p['player'].startswith('kbo:') or
                             any(not math.isfinite(p[k]) or p[k] < 0 for k in ['outs', 'runs', 'k', 'bb'])
                             or p['outs'] != int(p['outs']) for p in ps):
                raise ValueError('invalid_pitching_source')
            if sum(p['runs'] for p in ps) != r['ag' if i == 0 else 'hg']:
                raise ValueError('pitching_score_conflict')
        fixtures[key(r)].append(r)
    for r in scores:
        k = key(r)
        if k in anchors and (r['hg'], r['ag']) != (anchors[k]['hg'], anchors[k]['ag']):
            raise ValueError('conflicting_score_anchor')
        if fixtures.get(k) and not any((r['hg'], r['ag']) == (q['hg'], q['ag']) for q in fixtures[k]):
            raise ValueError('score_pitching_conflict')
        anchors[k] = r
    fields = ['date', 'home', 'away', 'hg', 'ag']
    games = [{k: r[k] for k in fields} | {'source_fixture_id': r['official_id']} for r in pitching]
    games += [{k: r[k] for k in fields} | {'source_fixture_id': 'score:'+':'.join(key(r))}
              for r in anchors.values() if key(r) not in fixtures]
    allstar = [r for r in games if {r['home'], r['away']}-set(TEAMS)]
    games = sorted([r for r in games if not ({r['home'], r['away']}-set(TEAMS))],
                   key=lambda r: (r['date'], r['source_fixture_id']))
    for r in games:
        date.fromisoformat(r['date'])
        if any(r[k] < 0 or int(r[k]) != r[k] for k in ['hg', 'ag']) or r['home'] == r['away']:
            raise ValueError('invalid_game')
    expected = {'train': ('2023', 710), 'validation': ('2024', 712), 'evaluation': ('2026', 198)}
    if Counter(r['role'] for r in cohorts) != {k: v[1] for k, v in expected.items()}:
        raise ValueError('cohort_count_changed')
    for r in cohorts:
        if r['date'][:4] != expected[r['role']][0]:
            raise ValueError('cohort_year_changed')
        matches = fixtures.get(key(r), [])
        if len(matches) > 1 or (matches and (matches[0]['hg'], matches[0]['ag']) != (r['hg'], r['ag'])):
            raise ValueError('target_fixture_conflict')
        if r['role'] != 'evaluation' and len(matches) != 1:
            raise ValueError('historical_target_missing')
        r['source_fixture_id'] = matches[0]['official_id'] if matches else 'cohort:'+r['fixture_id']
        # Preserve the predecessor completeness guard, even though this model uses only scores.
        d = date.fromisoformat(r['date'])
        if any(0 < (d-date.fromisoformat(q['date'])).days <= 28 and
               {r['home'], r['away']} & {q['home'], q['away']} and k not in fixtures
               for k, q in anchors.items()):
            raise ValueError('prior28_pitching_gap')
        market(r)
        reference(r)
    cohorts.sort(key=lambda r: (r['date'], r.get('fixture_id', ''), r['home'], r['away']))
    if sum(r['date'] < '2026-07-01' for r in cohorts if r['role'] == 'evaluation') != 152:
        raise ValueError('evaluation_period_changed')
    audit = dict(cohort_counts=dict(Counter(r['role'] for r in cohorts)), pitching_games=len(pitching),
                 score_anchors=len(scores), merged_score_games=len(games), allstar_excluded=len(allstar),
                 doubleheader_extra_games=len(pitching)-len(fixtures),
                 synthetic_year_ids=sum(r['official_id'][:4] != r['date'][:4] for r in pitching),
                 games_by_year=dict(Counter(r['date'][:4] for r in games)), original_evaluation=203,
                 existing_excluded=5, used_inputs='historical final scores; no same-day pitching/batting')
    return cohorts, games, audit


class OnlineState:
    """One day = one update period; all gradients use the same prior snapshot."""
    def __init__(self, spec):
        self.spec = spec
        self.m = np.zeros((len(TEAMS), 1 if spec['family'] == 'elo' else 2))
        self.v = np.full_like(self.m, .08)
        self.count = np.zeros(len(TEAMS), dtype=int)
        self.last_day = None
        self.latest_observation = None
        self.total_observations = 0

    def advance(self, day):
        if self.last_day is not None:
            d = (date.fromisoformat(day)-date.fromisoformat(self.last_day)).days
            if d < 0:
                raise ValueError('time_reversal')
            year_gap = int(day[:4])-int(self.last_day[:4])
            season = self.spec['season_keep']**year_gap
            a = 2**(-d/self.spec['half_life'])*season
            self.m *= a
            if self.spec['family'] != 'elo':
                self.v = np.minimum(.25, a*a*self.v+self.spec['process']*d +
                                    (.08*(1-season*season) if year_gap else 0))
        self.last_day = day

    def observe_pair(self, home, away):
        h, a = TEAMS.index(home), TEAMS.index(away)
        if self.spec['family'] == 'elo':
            x = [float(self.m[h, 0]-self.m[a, 0])]
            edge = .12+x[0]
            means = None
        else:
            uncertainty = float(self.v[h].sum()+self.v[a].sum())
            c = math.sqrt(1+math.pi*1.8**2*uncertainty/8)
            x = [(self.m[h, 0]-self.m[a, 0])/c, (self.m[h, 1]-self.m[a, 1])/c]
            edge = 1.8*(.12+float((self.m[h]-self.m[a]).sum()))/c
            means = [4.5*math.exp(float(np.clip(.06+self.m[h, 0]-self.m[a, 1], -2, 2))),
                     4.5*math.exp(float(np.clip(-.06+self.m[a, 0]-self.m[h, 1], -2, 2)))]
        return dict(x=[float(z) for z in x], state_logit=float(edge), run_means=means,
                    home_state=self.m[h].tolist(), away_state=self.m[a].tolist(),
                    home_variance=self.v[h].tolist(), away_variance=self.v[a].tolist(),
                    home_games=int(self.count[h]), away_games=int(self.count[a]),
                    latest_observation=self.latest_observation, total_observations=self.total_observations)

    def update(self, games):
        grad, curvature = np.zeros_like(self.m), np.zeros_like(self.m)
        ordered = sorted(games, key=lambda r: r['source_fixture_id'])
        ids = [r['source_fixture_id'] for r in ordered]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate_update_fixture')
        for r in ordered:
            if r['date'] != self.last_day:
                raise ValueError('update_wrong_day')
            h, a = TEAMS.index(r['home']), TEAMS.index(r['away'])
            obs = self.observe_pair(r['home'], r['away'])
            if self.spec['family'] == 'elo':
                y = .5 if r['hg'] == r['ag'] else float(r['hg'] > r['ag'])
                w = min(2., math.log1p(abs(r['hg']-r['ag']))/math.log(4)) if y != .5 else 1.
                delta = self.spec['k']*w*(y-expit(obs['state_logit']))
                grad[h, 0] += delta
                grad[a, 0] -= delta
            else:
                for attack, defence, y, mu in [(h, a, r['hg'], obs['run_means'][0]),
                                                (a, h, r['ag'], obs['run_means'][1])]:
                    g, c = (y-mu)/2, mu/2
                    grad[attack, 0] += g
                    grad[defence, 1] -= g
                    curvature[attack, 0] += c
                    curvature[defence, 1] += c
            self.count[h] += 1
            self.count[a] += 1
        if self.spec['family'] == 'elo':
            self.m += grad
        else:
            self.v = 1/(1/self.v+curvature)
            self.m = np.clip(self.m+self.v*grad, -1, 1)
        if ordered:
            self.latest_observation = self.last_day
            self.total_observations += len(ordered)


def replay(cohorts, games, keep_snapshots=False):
    targets, observed = defaultdict(list), defaultdict(list)
    for i, r in enumerate(cohorts):
        targets[r['date']].append(i)
    for r in games:
        observed[r['date']].append(r)
    states = {name: OnlineState(spec) for name, spec in state_specs().items()}
    rows = [dict(r, dynamic={}) for r in cohorts]
    snapshots = []
    last_target = max(targets)
    for day in sorted(set(targets) | set(observed)):
        if day > last_target:
            break
        for name, state in states.items():
            state.advance(day)
            for i in targets[day]:
                r = rows[i]
                r['dynamic'][name] = state.observe_pair(r['home'], r['away'])
                latest = r['dynamic'][name]['latest_observation']
                if latest is not None and latest >= day:
                    raise ValueError('feature_leak')
            if keep_snapshots:
                snapshots.append(dict(date=day, state=name, mean=state.m.tolist(), variance=state.v.tolist(),
                                      counts=state.count.tolist(), latest_observation=state.latest_observation,
                                      total_observations=state.total_observations, teams=TEAMS))
            state.update(observed[day])
    return rows, snapshots


def fit(rows, spec):
    if any(r['role'] == 'evaluation' or r['date'][:4] not in {'2023', '2024'} for r in rows):
        raise ValueError('forbidden_calibration_year')
    config = dict(spec, training_n=len(rows), training_years=sorted({r['date'][:4] for r in rows}))
    if spec['kind'] != 'residual':
        return config
    settled = [r for r in rows if r['hg'] != r['ag']]
    x = np.array([r['dynamic'][spec['state']]['x'] for r in settled])
    scale = np.maximum(np.std(x, axis=0), 1e-6)
    x = np.clip(x/scale, -6, 6)
    offset = logit([reference(r) for r in settled])
    y = np.array([r['hg'] > r['ag'] for r in settled], dtype=float)

    def objective(beta):
        z = offset+x@beta
        return (float(np.mean(np.logaddexp(0, z)-y*z)+spec['ridge']*(beta@beta)/2),
                x.T@(expit(z)-y)/len(y)+spec['ridge']*beta)

    opt = minimize(objective, np.zeros(x.shape[1]), jac=True, method='BFGS',
                   options={'gtol': 1e-10, 'maxiter': 1000})
    gradient = float(np.max(np.abs(objective(opt.x)[1])))
    if not np.isfinite(opt.x).all() or gradient > 1e-7:
        raise ValueError('fit_failure:'+str(opt.message))
    config.update(scale=scale.tolist(), beta=opt.x.tolist(), gradient_max=gradient,
                  objective=float(opt.fun), settled_training_n=len(y))
    return config


def predict(config, rows):
    if config['kind'] == 'market_null':
        return np.array([market(r) for r in rows])
    base = np.array([reference(r) for r in rows])
    if config['kind'] == 'baseline':
        return base
    state = config['state']
    if config['kind'] == 'blend':
        edge = np.array([r['dynamic'][state]['state_logit'] for r in rows])
        return expit((1-config['weight'])*logit(base)+config['weight']*edge)
    x = np.array([r['dynamic'][state]['x'] for r in rows])
    return expit(logit(base)+np.clip(x/np.array(config['scale']), -6, 6)@np.array(config['beta']))


def select(candidates):
    m = lambda n: candidates[n]['validation']
    eligible = [n for n in candidates if m(n)['wins'] >= m('market_null')['wins'] and
                m(n)['net_krw'] >= m('market_null')['net_krw']]
    return dict(primary=min(eligible, key=lambda n: (m(n)['logloss'], m(n)['brier'], n)),
                hit_head=min(candidates, key=lambda n: (-m(n)['wins'], m(n)['brier'], n)),
                profit_head=min(candidates, key=lambda n: (-m(n)['net_krw'], m(n)['logloss'], n)),
                eligible=sorted(eligible))


def freeze_from_rows(rows):
    train = [r for r in rows if r['role'] == 'train']
    val = [r for r in rows if r['role'] == 'validation']
    candidates, vp = {}, {}
    for name, spec in candidate_specs().items():
        first = fit(train, spec)
        p = predict(first, val)
        vp[name] = p.tolist()
        candidates[name] = dict(specification=spec, selection_fit=first, validation=metrics(val, p),
                                validation_changes_vs_market=int(np.sum((p >= .5) !=
                                                    (np.array([market(r) for r in val]) >= .5))),
                                final_fit=fit(train+val, spec))
    return dict(heads=select(candidates), candidates=candidates), vp


def ledger(rows, probabilities):
    output = []
    for i, r in enumerate(rows):
        record = {k: v for k, v in r.items() if k != 'dynamic'}
        record.update(market_probability=market(r), baseline_probability=reference(r), candidates={})
        for name, values in probabilities.items():
            p = float(values[i])
            hit, profit, settled = settlement(r, p)
            prices, order = prices_order(r)
            side = 'home' if p >= .5 else 'away'
            record['candidates'][name] = dict(probability=p, side=side, price=prices[order.index(side)],
                                               hit=hit, profit_krw=profit, settled=settled, stake_krw=10000)
        output.append(record)
    return output


def freeze(folder):
    if (folder/'frozen-selection.json').exists():
        raise ValueError('already_frozen')
    cohorts, games, audit = load_facts(folder)
    rows, snapshots = replay(cohorts, games, True)
    frozen, vp = freeze_from_rows(rows)
    frozen.update(input_sha256=input_hashes(folder), source_audit=audit,
                  training_features_hash=canonical_hash([r for r in rows if r['role'] != 'evaluation']),
                  versions=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                  protocol_commit='dd342d11', state_specifications=state_specs())
    write_json(folder/'frozen-selection.json', frozen)
    write_json(folder/'source-audit.json', audit)
    write_rows(folder/'games.jsonl.gz', games)
    write_rows(folder/'features.jsonl.gz', rows)
    write_rows(folder/'daily-state-snapshots.jsonl.gz', snapshots)
    write_rows(folder/'validation-ledger.jsonl.gz', ledger([r for r in rows if r['role'] == 'validation'], vp))
    print(json.dumps({'heads': frozen['heads'], 'validation_market': frozen['candidates']['market_null']['validation']},
                     ensure_ascii=False))


def evaluate(folder):
    frozen = json.loads((folder/'frozen-selection.json').read_text(encoding='utf8'))
    if input_hashes(folder) != frozen['input_sha256']:
        raise ValueError('frozen_inputs_or_code_changed')
    cohorts, games, audit = load_facts(folder)
    rows, _ = replay(cohorts, games)
    if canonical_hash([r for r in rows if r['role'] != 'evaluation']) != frozen['training_features_hash']:
        raise ValueError('training_features_changed')
    evaluation = [r for r in rows if r['role'] == 'evaluation']
    probabilities = {n: predict(c['final_fit'], evaluation) for n, c in frozen['candidates'].items()}
    all_results = {}
    for name, p in probabilities.items():
        all_results[name] = dict(validation=frozen['candidates'][name]['validation'], evaluation=metrics(evaluation, p),
            changes_vs_baseline=int(np.sum((p >= .5) != (probabilities['baseline'] >= .5))),
            changes_vs_market=int(np.sum((p >= .5) != (probabilities['market_null'] >= .5))))
    periods = {}
    names = set(['baseline', 'market_null']+[frozen['heads'][h] for h in ['primary', 'hit_head', 'profit_head']])
    for label, indexes in [('all', list(range(len(evaluation)))),
                           ('pre_july', [i for i, r in enumerate(evaluation) if r['date'] < '2026-07-01']),
                           ('post_july', [i for i, r in enumerate(evaluation) if r['date'] >= '2026-07-01'])]:
        rr = [evaluation[i] for i in indexes]
        periods[label] = {}
        for name in sorted(names):
            pp = probabilities[name][indexes]
            periods[label][name] = dict(metrics=metrics(rr, pp),
                changes_vs_baseline=int(np.sum((pp >= .5) != (probabilities['baseline'][indexes] >= .5))),
                changes_vs_market=int(np.sum((pp >= .5) != (probabilities['market_null'][indexes] >= .5))),
                paired={base: paired_interval(rr, pp, probabilities[base][indexes]) for base in ['baseline', 'market_null']})
    write_rows(folder/'evaluation-ledger.jsonl.gz', ledger(evaluation, probabilities))
    result = dict(heads=frozen['heads'], candidates=all_results, periods=periods, source_audit=audit,
                  frozen_selection_sha256=normalized_hash(folder/'frozen-selection.json'),
                  protocol_commit=frozen['protocol_commit'], versions=frozen['versions'],
                  limitations=['retrospective reused evaluation, no untouched holdout',
                               'no archived information-availability/odds execution timestamp proof',
                               'evaluation reference has votes; validation reference does not',
                               'no lineup, starter, park or inning model; no claim of future profit'])
    write_json(folder/'results.json', result)
    print(json.dumps({'heads': frozen['heads'], 'evaluation': {n: periods['all'][n]['metrics'] for n in sorted(names)}},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'freeze', 'evaluate'])
    parser.add_argument('--folder', type=Path, default=DEFAULT)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.source is None:
            parser.error('--source is required for prepare')
        prepare(args.folder, args.source)
    elif args.command == 'freeze':
        freeze(args.folder)
    else:
        evaluate(args.folder)


if __name__ == '__main__':
    main()
