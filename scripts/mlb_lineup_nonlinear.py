"""Frozen, offline MLB announced-nine EB matchup experiment (NumPy only)."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform

import numpy as np

from mlb_lineup_nonlinear_collect import OUT, read_rows, write_json, write_rows, stamp

WEIGHTS = np.array([1.12, 1.09, 1.06, 1.03, 1., .97, .94, .91, .88])
WEIGHTS /= WEIGHTS.sum()
BAT_NAMES = ['xwoba', 'xslg', 'obp', 'slg', 'k', 'bb', 'missing_prior', 'missing_current', 'missing_savant']
PIT_NAMES = ['allowed_xwoba', 'xera', 'k', 'bb', 'kbb', 'era', 'current_reliability',
             'missing_prior', 'missing_current', 'missing_savant']
LINEAR_NAMES = ['lineup_' + n for n in BAT_NAMES] + ['starter_' + n for n in PIT_NAMES] + [
    'lineup_top3_xwoba', 'lineup_bottom3_xwoba', 'lineup_xwoba_spread', 'lineup_weak_share']
MATCH_NAMES = ['xwoba_cross', 'k_cross', 'bb_cross', 'slug_allowed_cross', 'top3_allowed_cross',
               'bottom3_allowed_cross', 'spread_k_cross', 'tanh_matchup']
CANDIDATES = {f'{family}:l2={ridge:g}': (family, ridge) for family in ['linear', 'matchup', 'hinge']
              for ridge in [.01, .1, 1., 10.]}


def number(value, default=0.):
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def load_tables(folder):
    tables = {}
    for year in [2023, 2024, 2025]:
        for kind, group in [('batter', 'hitting'), ('pitcher', 'pitching')]:
            sv = json.loads((folder / 'sources' / f'savant-{year}-{kind}.json').read_text(encoding='utf8'))
            assert sv['season'] == year and all(int(r['year']) == year for r in sv['rows'])
            srows = {int(r['player_id']): r for r in sv['rows']}
            assert len(srows) == len(sv['rows'])
            path = folder / 'sources' / f'mlb-stats-{year}-{group}.json.gz'
            rows = []
            if path.exists():
                with gzip.open(path, 'rt', encoding='utf8') as f:
                    raw = json.load(f)
                rows = raw['stats'][0]['splits']
                assert len(rows) == raw['stats'][0]['totalSplits']
                assert all(int(r['season']) == year for r in rows)
            stats = {r['player']['id']: r['stat'] for r in rows}
            assert len(stats) == len(rows), 'do not double count traded players'
            exposure = 'plateAppearances' if kind == 'batter' else 'battersFaced'
            total = sum(number(s.get(exposure)) for s in stats.values())
            def pooled(field, fallback):
                return sum(number(s.get(field)) for s in stats.values()) / total if total else fallback
            def svmean(field, fallback):
                valid = [s for s in srows.values() if s.get(field) not in [None, '']]
                denom = sum(number(s.get('pa')) for s in valid)
                return sum(number(s.get('pa')) * number(s[field]) for s in valid) / denom if denom else fallback
            ab = sum(number(s.get('atBats')) for s in stats.values())
            obpden = sum(number(s.get('atBats')) + number(s.get('baseOnBalls')) + number(s.get('hitByPitch')) +
                         number(s.get('sacFlies')) for s in stats.values())
            outs = sum(number(s.get('outs')) for s in stats.values())
            means = dict(k=pooled('strikeOuts', .23), bb=pooled('baseOnBalls', .085),
                         xwoba=svmean('est_woba', .32), xslg=svmean('est_slg', .41), xera=svmean('xera', 4.3),
                         obp=sum(number(s.get('hits')) + number(s.get('baseOnBalls')) + number(s.get('hitByPitch'))
                                 for s in stats.values()) / obpden if obpden else .32,
                         slg=sum(number(s.get('totalBases')) for s in stats.values()) / ab if ab else .41,
                         era=27 * sum(number(s.get('earnedRuns')) for s in stats.values()) / outs if outs else 4.3)
            tables[year, kind] = dict(stats=stats, savant=srows, means=means, season=year,
                                      stats_source=f'sources/mlb-stats-{year}-{group}.json.gz',
                                      savant_source=f'sources/savant-{year}-{kind}.json')
    return tables


def player_features(player, kind, table):
    """All denominators are PA/BF/actual outs; shrink toward previous-season population."""
    pid = int(player['id'])
    prev = table['stats'].get(pid, {})
    sv = table['savant'].get(pid, {})
    means = table['means']
    exposure = 'plateAppearances' if kind == 'batter' else 'battersFaced'
    n = number(prev.get(exposure))
    prior_strength = 150. if kind == 'batter' else 300.
    def rate(s, field, denom, default):
        return number(s.get(field)) / denom if denom > 0 else default
    def eb(value, count, mean, strength=prior_strength):
        return (value * count + strength * mean) / (count + strength)
    prior_k = eb(rate(prev, 'strikeOuts', n, means['k']), n, means['k'])
    prior_bb = eb(rate(prev, 'baseOnBalls', n, means['bb']), n, means['bb'])
    svpa = number(sv.get('pa'))
    xwoba = eb(number(sv.get('est_woba'), means['xwoba']), svpa, means['xwoba'])
    xslg = eb(number(sv.get('est_slg'), means['xslg']), svpa, means['xslg'])
    xera = eb(number(sv.get('xera'), means['xera']), svpa, means['xera'])
    current = player.get('batting' if kind == 'batter' else 'pitching', {})
    curr_n = number(current.get(exposure))
    strength = 150. if kind == 'batter' else 100.
    k = eb(rate(current, 'strikeOuts', curr_n, prior_k), curr_n, prior_k, strength)
    bb = eb(rate(current, 'baseOnBalls', curr_n, prior_bb), curr_n, prior_bb, strength)
    missing = [float(n == 0), float(curr_n == 0), float(not sv or svpa == 0)]
    if kind == 'batter':
        def obp(s):
            denom = sum(number(s.get(f)) for f in ['atBats', 'baseOnBalls', 'hitByPitch', 'sacFlies'])
            numer = sum(number(s.get(f)) for f in ['hits', 'baseOnBalls', 'hitByPitch'])
            return numer / denom if denom else means['obp'], denom
        pr_obp, pn = obp(prev)
        pr_obp = eb(pr_obp, pn, means['obp'])
        pr_ab = number(prev.get('atBats'))
        pr_slg = eb(rate(prev, 'totalBases', pr_ab, means['slg']), pr_ab, means['slg'])
        cu_obp, cn = obp(current)
        cu_ab = number(current.get('atBats'))
        values = [xwoba, xslg, eb(cu_obp, cn, pr_obp, 150.),
                  eb(rate(current, 'totalBases', cu_ab, pr_slg), cu_ab, pr_slg, 150.), k, bb] + missing
    else:
        pr_outs = number(prev.get('outs'))
        pr_era = eb(rate(prev, 'earnedRuns', pr_outs / 27, means['era']), n, means['era'], 300.)
        cu_outs = number(current.get('outs'))
        era = eb(rate(current, 'earnedRuns', cu_outs / 27, pr_era), cu_outs / 3, pr_era, 30.)
        values = [xwoba, xera, k, bb, k - bb, era, curr_n / (curr_n + 100.)] + missing
    return np.array(values), dict(id=pid, prior_season=table['season'], prior_exposure=n,
                                  current_exposure=curr_n, savant_pa=svpa, rates=values)


def feature_row(row, tables):
    prior_year = int(row['start_at'][:4]) - 1
    values, proof = {}, {}
    for side in ['home', 'away']:
        source = row['sides'][side]
        order = [p['id'] for p in source['lineup']]
        assert len(order) == len(set(order)) == 9
        bat, pit = tables[prior_year, 'batter'], tables[prior_year, 'pitcher']
        assert bat['season'] == pit['season'] == prior_year
        rates = [player_features(p, 'batter', bat) for p in source['lineup']]
        b = np.array([r[0] for r in rates])
        p, pproof = player_features(source['starter'], 'pitcher', pit)
        lineup = WEIGHTS @ b
        spread = float(np.sqrt(WEIGHTS @ (b[:, 0] - lineup[0]) ** 2))
        extras = np.array([b[:3, 0].mean(), b[6:, 0].mean(), spread, WEIGHTS @ (b[:, 0] < .300)])
        values[side] = lineup, p, extras
        proof[side] = dict(lineup_order=order, batters=[r[1] for r in rates], starter=pproof,
                           weighted_batting=lineup.tolist(), lineup_distribution=extras.tolist())
    h, a = values['home'], values['away']
    # Starter differences are home-away; coefficients learn which direction is favorable.
    linear = np.r_[h[0] - a[0], h[1] - a[1], h[2] - a[2]]
    def matchup(offense, pitcher):
        b, _, ex = offense
        _, p, _ = pitcher
        bx = (b[0] - .32) / .05
        px = (p[0] - .32) / .05
        return np.array([bx * px, ((b[4] - .23) / .07) * ((p[2] - .23) / .07),
                         ((b[5] - .085) / .03) * ((p[3] - .085) / .03),
                         ((b[1] - .41) / .08) * px, ((ex[0] - .32) / .05) * px,
                         ((ex[1] - .32) / .05) * px, (ex[2] / .04) * ((p[2] - .23) / .07),
                         math.tanh(bx + px - (p[4] - .145) / .07)])
    cross = matchup(h, a) - matchup(a, h)
    return np.r_[linear, cross], dict(prior_season=prior_year, sides=proof,
              sources=[tables[prior_year, kind][field] for kind in ['batter', 'pitcher']
                       for field in ['stats_source', 'savant_source']], weights=WEIGHTS.tolist())


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -40, 40)))


def design(model, raw):
    size = len(LINEAR_NAMES) if model['family'] == 'linear' else len(LINEAR_NAMES) + len(MATCH_NAMES)
    z = np.clip((np.asarray(raw)[:, :size] - np.array(model['center'])) / np.array(model['scale']), -6, 6)
    if model['family'] == 'hinge':
        base = z[:, :len(LINEAR_NAMES)]
        z = np.c_[z, np.maximum(base, 0) ** 2, np.maximum(-base, 0) ** 2]
    return np.c_[np.ones(len(z)), z]


def fit(raw, y, family, ridge):
    size = len(LINEAR_NAMES) if family == 'linear' else len(LINEAR_NAMES) + len(MATCH_NAMES)
    raw = np.asarray(raw)[:, :size]
    center, scale = raw.mean(axis=0), raw.std(axis=0)
    scale[scale < 1e-8] = 1.
    model = dict(family=family, ridge=ridge, center=center.tolist(), scale=scale.tolist(),
                 feature_names=(LINEAR_NAMES if family == 'linear' else LINEAR_NAMES + MATCH_NAMES) +
                 (['positive_square_' + n for n in LINEAR_NAMES] + ['negative_square_' + n for n in LINEAR_NAMES]
                  if family == 'hinge' else []), train_n=len(y))
    x = design(model, raw)
    y = np.asarray(y, dtype=float)
    beta = np.zeros(x.shape[1])
    penalty = np.r_[0., np.repeat(ridge, len(beta) - 1)]
    def loss(b):
        z = x @ b
        return float(np.mean(np.logaddexp(0, z) - y * z) + .5 * np.dot(penalty * b, b))
    converged = False
    for iteration in range(100):
        p = sigmoid(x @ beta)
        gradient = x.T @ (p - y) / len(y) + penalty * beta
        if np.max(np.abs(gradient)) < 1e-10:
            converged = True
            break
        hess = (x.T * (p * (1 - p))) @ x / len(y) + np.diag(penalty + 1e-12)
        step = np.linalg.solve(hess, gradient)
        alpha, old = 1., loss(beta)
        while loss(beta - alpha * step) > old - 1e-4 * alpha * float(gradient @ step):
            alpha *= .5
            if alpha < 1e-10:
                break
        beta -= alpha * step
        if np.max(np.abs(alpha * step)) < 1e-10:
            converged = True
            break
    final_grad = x.T @ (sigmoid(x @ beta) - y) / len(y) + penalty * beta
    assert np.max(np.abs(final_grad)) < 1e-7, 'optimizer failed'
    model.update(beta=beta.tolist(), iterations=iteration + 1, converged=converged,
                 max_gradient=float(np.max(np.abs(final_grad))), objective=loss(beta))
    return model


def predict(model, raw):
    return sigmoid(design(model, raw) @ np.array(model['beta']))


def scores(y, p):
    y, p = np.asarray(y, dtype=float), np.clip(np.asarray(p), 1e-12, 1 - 1e-12)
    return dict(n=len(y), correct=int(np.sum((p >= .5) == y)), hit=float(np.mean((p >= .5) == y)),
                logloss=float(np.mean(-y * np.log(p) - (1 - y) * np.log(1 - p))),
                brier=float(np.mean((p - y) ** 2)))


def select_heads(validation):
    return dict(primary=min(validation, key=lambda n: (validation[n]['logloss'], validation[n]['brier'], n)),
                hit=min(validation, key=lambda n: (-validation[n]['correct'], validation[n]['logloss'], n)))


def split_rows(rows, external_ids=()):
    evaluation = [r for r in rows if r['accepted'] and r['role'] == 'evaluation']
    forbidden = {r['gamePk'] for r in evaluation} | set(external_ids)
    train = [r for r in rows if r['accepted'] and r['role'] == 'train' and r['gamePk'] not in forbidden]
    val = [r for r in rows if r['accepted'] and r['role'] == 'validation' and r['gamePk'] not in forbidden]
    overlap = sorted({r['gamePk'] for r in rows if r['role'] == 'validation'} & forbidden)
    for rs, year in [(train, '2024'), (val, '2025')]:
        assert all(r['start_at'].startswith(year) for r in rs)
        assert len(rs) == len({r['gamePk'] for r in rs})
    assert {r['gamePk'] for r in train}.isdisjoint(r['gamePk'] for r in val)
    assert max(r['start_at'] for r in train) < min(r['start_at'] for r in val)
    assert max(r['start_at'] for r in val) < min(r['start_at'] for r in evaluation)
    return train, val, evaluation, overlap


def settle(p, hg, ag, prices):
    pick = 'home' if p >= .5 else 'away'
    if hg == ag:
        return dict(pick=pick, correct=0, push=1, net_krw=0)
    correct = (hg > ag) == (pick == 'home')
    price = Decimal(str(prices[0 if pick == 'home' else 1]))
    net = int((Decimal(10000) * (price - 1)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if correct else -10000
    return dict(pick=pick, correct=int(correct), push=0, net_krw=net)


def wilson(k, n):
    if not n:
        return [None, None]
    z = 1.959963984540054
    center = (k / n + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [center - half, center + half]


def paired_ci(rows, policy, baseline='baseline'):
    groups = defaultdict(lambda: np.zeros(5))
    for r in rows:
        d = date.fromisoformat(r['date'])
        week = (d - timedelta(days=d.weekday())).isoformat()
        a, b = r['ledger'][policy], r['ledger'][baseline]
        y = float(r['hg'] > r['ag'])
        pa, pb = r['probabilities'][policy], r['probabilities'][baseline]
        la = -y * math.log(max(pa, 1e-12)) - (1-y) * math.log(max(1-pa, 1e-12))
        lb = -y * math.log(max(pb, 1e-12)) - (1-y) * math.log(max(1-pb, 1e-12))
        groups[week] += [a['correct'] - b['correct'], (a['net_krw'] - b['net_krw']) / 10000,
                         la - lb, (pa-y)**2 - (pb-y)**2, 1]
    if not groups:
        return {}
    values = np.array(list(groups.values()))
    rng = np.random.default_rng(20261029)
    sums = values[rng.integers(len(values), size=(5000, len(values)))].sum(axis=1)
    return dict(weeks=len(groups), **{name: np.quantile(sums[:, i] / sums[:, 4], [.025, .975]).tolist()
                 for i, name in enumerate(['hit_delta95', 'roi_delta95', 'logloss_delta95', 'brier_delta95'])})


def summarize(rows, policy, total_requested):
    if not rows:
        return dict(n=0, coverage=0., requested=total_requested)
    rows = sorted(rows, key=lambda r: (r['start_at'], r['gamePk']))
    led = [r['ledger'][policy] for r in rows]
    wealth = np.cumsum([0] + [r['net_krw'] for r in led])
    result = scores([r['hg'] > r['ag'] for r in rows], [r['probabilities'][policy] for r in rows])
    correct = sum(r['correct'] for r in led)
    result.update(correct=correct, pushes=sum(r['push'] for r in led),
                  hit=correct / max(1, len(led) - sum(r['push'] for r in led)),
                  hit95=wilson(correct, len(led) - sum(r['push'] for r in led)),
                  net_krw=int(wealth[-1]), roi=float(wealth[-1] / (10000 * len(rows))),
                  max_drawdown_krw=int(np.max(np.maximum.accumulate(wealth) - wealth)),
                  changed_picks=sum(r['ledger'][policy]['pick'] != r['ledger']['baseline']['pick'] for r in rows),
                  requested=total_requested, coverage=len(rows)/total_requested,
                  announced_n=sum(r['announced'] for r in rows),
                  announced_coverage=sum(r['announced'] for r in rows)/total_requested,
                  paired95=paired_ci(rows, policy))
    return result


def verify_sources(folder):
    manifest = json.loads((folder / 'sources/manifest.json').read_text(encoding='utf8'))
    for item in manifest:
        data = (folder / item['path']).read_bytes()
        if not item['path'].endswith('.gz'):
            data = data.replace(b'\r\n', b'\n')
        assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
    return manifest


def run(folder=OUT):
    manifest = verify_sources(folder)
    rows = sorted(read_rows(folder / 'sources/mlb-snapshots.jsonl.gz'), key=lambda r: (r['start_at'], r['gamePk']))
    external = sorted(read_rows(folder / 'sources/mlb-external-features.jsonl'), key=lambda r: (r['scheduled_start'], r['gamePk']))
    ex_snapshots = {r['gamePk']: r for r in read_rows(folder / 'external-snapshots.jsonl.gz')}
    assert set(ex_snapshots) == {r['gamePk'] for r in external}
    train, validation, evaluation, overlap = split_rows(rows, [r['gamePk'] for r in external])
    tables = load_tables(folder)
    all_valid = [r for r in rows if r['accepted']] + [r for r in ex_snapshots.values() if r['accepted']]
    features, input_rows = {}, []
    for row in all_valid:
        assert stamp(row['snapshot_at']) <= stamp(row['cutoff_at']) < stamp(row['start_at'])
        values, proof = feature_row(row, tables)
        features[row['gamePk']] = values
        input_rows.append(dict(gamePk=row['gamePk'], role=row['role'], start_at=row['start_at'],
                          snapshot_at=row['snapshot_at'], cutoff_at=row['cutoff_at'],
                          url=row['url'], response_sha256=row['response_sha256'],
                          features=values.tolist(), proof=proof))
    write_rows(folder / 'inputs.jsonl.gz', sorted(input_rows, key=lambda r: (r['start_at'], r['gamePk'])))
    def x(rs):
        return np.array([features[r['gamePk']] for r in rs])
    def y(rs):
        assert all(r['home_score'] != r['away_score'] for r in rs)
        return np.array([int(r['home_score'] > r['away_score']) for r in rs])
    models, validation_scores, validation_probs = {}, {}, {}
    for name, (family, ridge) in CANDIDATES.items():
        m24 = fit(x(train), y(train), family, ridge)
        m25 = fit(x(train + validation), y(train + validation), family, ridge)
        p = predict(m24, x(validation))
        validation_scores[name] = scores(y(validation), p)
        validation_probs[name] = p
        models[name] = dict(before2025=m24, before2026=m25)
    heads = select_heads(validation_scores)
    frozen = dict(heads=heads, validation=validation_scores, models=models,
                  train_ids=[r['gamePk'] for r in train], validation_ids=[r['gamePk'] for r in validation],
                  excluded_validation_overlap_ids=overlap,
                  selection='pure validation logloss/Brier/name; hit correct/logloss/name; no validation odds',
                  protocol_commit='f1896a92', candidate_count=len(CANDIDATES),
                  feature_names=LINEAR_NAMES + MATCH_NAMES)
    # Selection and coefficient artifacts are written before ANY evaluation prediction/metric.
    write_json(folder / 'frozen-selection.json', frozen)
    val_ledger = [dict(gamePk=r['gamePk'], start_at=r['start_at'], y=int(y([r])[0]),
                       candidate_probabilities={n:float(p[i]) for n,p in validation_probs.items()})
                  for i,r in enumerate(validation)]
    write_rows(folder / 'validation-predictions.jsonl', val_ledger)
    old_ref = {r['gamePk']: r for r in read_rows(folder / 'sources/mlb-evaluation.jsonl')}
    pending = []
    for row in evaluation:
        t = row['target']
        assert (row['home_score'], row['away_score']) == (t['hg'], t['ag'])
        ref = old_ref[row['gamePk']]['probabilities']
        prices = [t['prices'][t['order'].index(s)] for s in ['home', 'away']]
        pending.append(dict(gamePk=row['gamePk'], cohort='old116', date=t['date'], start_at=row['start_at'],
                            hg=t['hg'], ag=t['ag'], prices=prices, price_order=['home','away'],
                            baseline=ref['market90_vote10'], market=ref['market'], announced=True,
                            baseline_kind='research_market90_vote10_not_operating_model',
                            quote_decision_at=None, announcement_cutoff=row['cutoff_at']))
    for row in external:
        sn = ex_snapshots[row['gamePk']]
        pending.append(dict(gamePk=row['gamePk'], cohort='external230', date=row['date'],
                            start_at=stamp(row['scheduled_start']).isoformat(), hg=row['hg'], ag=row['ag'],
                            prices=row['historical_prices'], price_order=['home','away'],
                            baseline=row['market_probability'], market=row['market_probability'],
                            announced=sn['accepted'], rejection_reason=sn.get('reason'),
                            baseline_kind='market_only_no_tipster_not_operating_model',
                            quote_decision_at=row['decision_at'], announcement_cutoff=sn.get('cutoff_at')))
    predictions = []
    for row in sorted(pending, key=lambda r:(r['start_at'],r['gamePk'])):
        p0 = row.pop('baseline')
        market = row.pop('market')
        candidates = {}
        stage = 'before2025' if row['start_at'].startswith('2025') else 'before2026'
        assert max(r['start_at'] for r in (train if stage=='before2025' else train+validation)) < row['start_at']
        if row['announced']:
            raw = np.array([features[row['gamePk']]])
            candidates = {n:float(predict(mm[stage], raw)[0]) for n,mm in models.items()}
        probs = dict(baseline=p0, market_only=market)
        for head, candidate in heads.items():
            probs[head] = candidates.get(candidate, p0)
            probs[head + '_blend10'] = .9*p0 + .1*probs[head]
        ledger = {n:settle(p,row['hg'],row['ag'],row['prices']) for n,p in probs.items()}
        predictions.append(dict(**row, model_stage=stage, selected_names=heads,
                                probabilities=probs, candidate_probabilities=candidates, ledger=ledger))
    write_rows(folder / 'predictions.jsonl', predictions)
    results = dict(runtime=dict(python=platform.python_version(), numpy=np.__version__),
                   input_counts=dict(train=len(train), validation=len(validation), evaluation=len(evaluation),
                                     external=len(external), external_announced=sum(r['accepted'] for r in ex_snapshots.values()),
                                     validation_overlap_excluded=overlap), heads=heads, groups={},
                   source_manifest_sha256=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),
                   retrospective=True, operational_model_reproduced=False)
    for cohort, split in [('old116','2026-07-01'),('external230','2026-08-10')]:
        cohort_rows = [r for r in predictions if r['cohort']==cohort]
        for scope in ['verified','full_fallback']:
            rs = [r for r in cohort_rows if r['announced'] or scope=='full_fallback']
            periods = dict(all=rs, before_split=[r for r in rs if r['date']<split],
                           split_onward=[r for r in rs if r['date']>=split])
            for month in sorted({r['date'][:7] for r in rs}):
                periods['month_'+month] = [r for r in rs if r['date'].startswith(month)]
            for period, prs in periods.items():
                requested = len(cohort_rows) if period=='all' else len([r for r in cohort_rows if
                    (r['date']<split if period=='before_split' else r['date']>=split if period=='split_onward'
                     else r['date'].startswith(period[6:]))])
                results['groups'][f'{cohort}/{scope}/{period}'] = {
                    policy:summarize(prs,policy,requested) for policy in
                    ['baseline','market_only','primary','hit','primary_blend10','hit_blend10']}
    write_json(folder / 'results.json', results)
    print(json.dumps(dict(counts=results['input_counts'], heads=heads,
          overall={k:{h:{m:s.get(m) for m in ['n','correct','roi','net_krw','changed_picks','max_drawdown_krw']}
                      for h,s in v.items()} for k,v in results['groups'].items() if k.endswith('/all')})), flush=True)
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,default=OUT)
    run(p.parse_args().folder)
