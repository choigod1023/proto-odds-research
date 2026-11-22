"""Independent arithmetic reference for conserved-volume spatial experiments.

Uses frozen source/forecast artifacts, never searches a parameter or selects a model.
"""
import argparse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

EPS = 1e-6
FAMILIES = ((0, 1, 2, 3, 4), (5, 6, 7, 8))


def read(path):
    path = Path(path)
    body = path.read_bytes()
    raw = gzip.decompress(body) if path.suffix == '.gz' else body
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def clip(p):
    return min(1-EPS, max(EPS, p))


def logit(p):
    p = clip(p)
    return math.log(p/(1-p))


def sigmoid(z):
    return 1/(1+math.exp(-z))


def sums(sides):
    vectors = [[s['estimated_possessions']]+s['attempts']+s['makes']+[s['fta'], s['ftm']] for s in sides]
    return [sum(v) for v in zip(*vectors)]


def rate_accuracy(own_p, defense_p, league_p, oa, da, la, om, dm, lm, strength):
    if la <= 0:
        return 0., 0.
    prior_rate = la/league_p
    rate = min(1.5, max(0., (oa+500*prior_rate)/(own_p+500)*(da+500*prior_rate)/(defense_p+500)/prior_rate))
    base = clip(lm/la)
    po = clip((om+strength*base)/(oa+strength))
    pd = clip((dm+strength*base)/(da+strength))
    return rate, clip(sigmoid(logit(po)+logit(pd)-logit(base)))


def family_components(own, defense, league):
    """Return Q, S, H rates plus exact symmetric attribution of S minus Q."""
    result = []
    for family in FAMILIES:
        la = sum(league[1+i] for i in family)
        q_rate, q_accuracy = rate_accuracy(own[0], defense[0], league[0],
            sum(own[1+i] for i in family), sum(defense[1+i] for i in family), la,
            sum(own[10+i] for i in family), sum(defense[10+i] for i in family),
            sum(league[10+i] for i in family), 200)
        zones = [rate_accuracy(own[0], defense[0], league[0], own[1+i], defense[1+i],
            league[1+i], own[10+i], defense[10+i], league[10+i], 200*league[1+i]/la) for i in family]
        s_rate = sum(r for r, p in zones)
        s_made = sum(r*p for r, p in zones)
        s_accuracy = s_made/s_rate if s_rate else q_accuracy
        volume = .5*(s_rate-q_rate)*(s_accuracy+q_accuracy)
        accuracy = .5*(s_accuracy-q_accuracy)*(s_rate+q_rate)
        result.append(dict(q_rate=q_rate, s_rate=s_rate, q_accuracy=q_accuracy, s_accuracy=s_accuracy,
            q_made=q_rate*q_accuracy, s_made=s_made, conserved_made=q_rate*s_accuracy,
            rate_ratio=s_rate/q_rate, volume_effect=volume, accuracy_effect=accuracy,
            conservation_error=q_rate-sum(q_rate*r/s_rate for r, p in zones),
            weights=[r/s_rate for r, p in zones], accuracy=[p for r, p in zones]))
    ft = rate_accuracy(own[0], defense[0], league[0], own[-2], defense[-2], league[-2],
                       own[-1], defense[-1], league[-1], 200)
    return result, ft[0]*ft[1]


def context(pool, row):
    day = date.fromisoformat(row['day'])
    active = sorted((g for g in pool if day-timedelta(days=365) <= date.fromisoformat(g['day']) <= day-timedelta(days=4)),
                    key=lambda g: (g['day'], g['date_utc'], g['game_id']))
    league = sums(s for g in active for s in g['sides'])
    own, defense, histories, exposures = [], [], [], []
    for team in row['teams']:
        games = [g for g in active if team in g['teams']]
        if len(games) < 5:
            raise ValueError('insufficient_history')
        os, ds = [], []
        for game in games:
            i = game['teams'].index(team)
            os.append(game['sides'][i]); ds.append(game['sides'][1-i])
        o, d = sums(os), sums(ds)
        own.append(o); defense.append(d)
        histories.append([g['game_id'] for g in games])
        exposures.append((o[0]+d[0])/(2*len(games)))
    return own, defense, league, math.sqrt(exposures[0]*exposures[1]), histories, len(active)


def score_probability(means):
    """Compound-Poisson probability via PGF recurrence, independent of convolution."""
    distributions = []
    for values in means:
        lambdas = dict(zip((2, 3, 1), values))
        p = [math.exp(-sum(values))]
        for k in range(1, 601):
            p.append(sum(j*lam*p[k-j] for j, lam in lambdas.items() if j <= k)/k)
        if abs(sum(p)-1) > 1e-10:
            raise ValueError('score_tail')
        distributions.append(p)
    cumulative = result = 0.
    for h, a in zip(*distributions):
        result += h*(cumulative+.5*a)
        cumulative += a
    return result


def reference(pool, row, include_probability=False):
    own, defense, league, exposure, histories, n = context(pool, row)
    components = [family_components(own[i], defense[1-i], league) for i in range(2)]
    means = {name: [[exposure*f[field] for f in families]+[exposure*ft]
                   for families, ft in components]
             for name, field in [('nonspatial', 'q_made'), ('spatial', 's_made'), ('conserved', 'conserved_made')]}
    result = dict(components=[c[0] for c in components], means=means,
                  history_game_ids=histories, asof_prior_games=n, exposure=exposure)
    if include_probability:
        result['probabilities'] = {name: score_probability(v) for name, v in means.items()}
    return result


def metrics(rows, model):
    daily = defaultdict(Decimal)
    hits = 0
    losses, briers = [], []
    for row in rows:
        p, y = row['probabilities'][model], row['home_win']
        side = int(p < .5)
        win = side == 1-y
        hits += win
        daily[row['day']] += Decimal(str(row['odds'][side]))-1 if win else Decimal(-1)
        losses.append(-math.log(clip(p) if y else 1-clip(p)))
        briers.append((p-y)**2)
    total = peak = mdd = Decimal(0)
    for day in sorted(daily):
        total += daily[day]
        peak = max(peak, total)
        mdd = max(mdd, peak-total)
    n = len(rows)
    return dict(n=n, wins=hits, hit_rate=hits/n, roi=float(sum(daily.values())/n),
                log_loss=sum(losses)/n, brier=sum(briers)/n, daily_mdd=float(mdd))


def beta_root(rows, candidate):
    """Independent root of penalized offset logistic gradient; L2=10."""
    pairs = [(logit(r['market']), logit(r[candidate])-logit(r['nonspatial']), r['home_win']) for r in rows]
    def gradient(beta):
        return sum(x*(sigmoid(z+beta*x)-y) for z, x, y in pairs)+10*beta
    if gradient(-10) >= 0:
        return -10.
    if gradient(10) <= 0:
        return 10.
    lo, hi = -10., 10.
    for _ in range(70):
        mid = (lo+hi)/2
        if gradient(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def describe(values):
    values = sorted(values)
    def quantile(q):
        t = (len(values)-1)*q
        i = int(t)
        return values[i]+(values[min(i+1, len(values)-1)]-values[i])*(t-i)
    return dict(n=len(values), mean=sum(values)/len(values), min=values[0],
                p05=quantile(.05), median=quantile(.5), p95=quantile(.95), max=values[-1])


def soft_weights(points, x, y):
    offset = 0 if points == 2 else 5
    count = 5 if points == 2 else 4
    if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y) or max(abs(x), abs(y)) > 1000:
        return [(offset+count-1, 1.)]
    radius = math.hypot(x-25, y)
    lo, hi = max(0., radius-1), radius+1
    edges = (0., 4., 10., 16., math.inf) if points == 2 else (0., 24., 27., math.inf)
    return [(offset+i, max(0., min(hi, upper)-max(lo, lower))/(hi-lo))
            for i, (lower, upper) in enumerate(zip(edges, edges[1:]))]


def verify_soft(hard, soft, cache):
    hmap = {r['game_id']: r for r in hard}
    if len(hmap) != 1185 or len(soft) != 1185 or {r['game_id'] for r in soft} != set(hmap):
        raise ValueError('soft_pool_changed')
    max_error = 0.
    attempts = 0
    for row in soft:
        original = hmap[row['game_id']]
        if any(row[k] != original[k] for k in ('day', 'date_utc', 'teams', 'source_summary_sha256')):
            raise ValueError('soft_identity_changed')
        path = Path(cache)/'summary'/(row['game_id']+'.json')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['source_summary_sha256']:
            raise ValueError('soft_source_hash')
        data = json.loads(raw)
        comp = data['header']['competitions'][0]
        team_index = {str(c['id']): 0 if c['homeAway']=='home' else 1 for c in comp['competitors']}
        counts = [dict(attempts=[0.]*9, makes=[0.]*9) for _ in range(2)]
        for play in data['plays']:
            if not play.get('shootingPlay') or play.get('pointsAttempted') not in (2, 3):
                continue
            coords = play.get('coordinate') or {}
            weights = soft_weights(play['pointsAttempted'], coords.get('x'), coords.get('y'))
            if abs(sum(w for _, w in weights)-1) > 1e-12:
                raise ValueError('soft_attempt_not_conserved')
            counter = counts[team_index[str(play['team']['id'])]]
            for i, weight in weights:
                counter['attempts'][i] += weight
                counter['makes'][i] += weight*bool(play['scoringPlay'])
            attempts += 1
        for actual, expected, base in zip(row['sides'], counts, original['sides'], strict=True):
            for key in ('fta', 'ftm', 'estimated_possessions'):
                if abs(actual[key]-base[key]) > 1e-10:
                    raise ValueError('soft_nonlocation_changed')
            for key in ('attempts', 'makes'):
                if len(actual[key]) != 9 or len(base[key]) != 9:
                    raise ValueError('soft_zone_shape')
                error = max(abs(a-b) for a, b in zip(actual[key], expected[key]))
                max_error = max(max_error, error)
                if error > 1e-9:
                    raise ValueError('soft_recount_mismatch')
                for family in FAMILIES:
                    if abs(sum(actual[key][i] for i in family)-sum(base[key][i] for i in family)) > 1e-9:
                        raise ValueError('soft_family_not_conserved')
    return dict(games=1185, raw_shots_recounted=attempts, max_count_error=max_error,
                interval_radius=1., official_geometry=False)


def diagnosis(pool, rows):
    output = []
    for row in rows:
        computed = reference(pool, row)
        if computed['history_game_ids'] != row['history_game_ids'] or computed['asof_prior_games'] != row['asof_prior_games']:
            raise ValueError('causal_context_changed')
        for name in ('nonspatial', 'spatial'):
            actual = [sum(x*w for x, w in zip(side, (2, 3, 1))) for side in computed['means'][name]]
            if max(abs(a-b) for a, b in zip(actual, row[name+'_expected_points'])) > 1e-9:
                raise ValueError('old_expected_points_not_reproduced')
        for side, components in enumerate(computed['components']):
            for family, c in zip(('two', 'three'), components):
                if abs(c['volume_effect']+c['accuracy_effect']-(c['s_made']-c['q_made'])) > 1e-12:
                    raise ValueError('attribution_identity')
                output.append(dict(id=row['id'], period=row['period'], side=side, family=family,
                                   point_weight=2 if family=='two' else 3, exposure=computed['exposure'], **c))
    summary = {}
    for period in ('A', 'B'):
        for family in ('two', 'three'):
            subset = [r for r in output if r['period']==period and r['family']==family]
            summary[period+'_'+family] = dict(rate_ratio=describe([r['rate_ratio'] for r in subset]),
                mean_volume_points=sum(r['exposure']*r['point_weight']*r['volume_effect'] for r in subset)/len(subset),
                mean_accuracy_points=sum(r['exposure']*r['point_weight']*r['accuracy_effect'] for r in subset)/len(subset),
                max_conservation_error=max(abs(r['conservation_error']) for r in subset))
        team_effects = defaultdict(lambda: [0., 0.])
        for row in output:
            if row['period'] == period:
                key = (row['id'], row['side'])
                scale = row['exposure']*row['point_weight']
                team_effects[key][0] += scale*row['volume_effect']
                team_effects[key][1] += scale*row['accuracy_effect']
        changed = [r for r in rows if r['period']==period and
                   (r['probabilities']['nonspatial'] >= .5) != (r['probabilities']['spatial'] >= .5)]
        q_wins = sum((r['probabilities']['nonspatial'] >= .5)==bool(r['home_win']) for r in changed)
        summary[period+'_attribution'] = dict(changed_picks=len(changed), q_win_s_loss=q_wins,
            q_loss_s_win=len(changed)-q_wins,
            mean_abs_team_volume_points=sum(abs(v[0]) for v in team_effects.values())/len(team_effects),
            mean_abs_team_accuracy_points=sum(abs(v[1]) for v in team_effects.values())/len(team_effects),
            max_abs_team_volume_points=max(abs(v[0]) for v in team_effects.values()))
    return dict(evaluation_games=len(rows), team_family_rows=len(output), summaries=summary), output


def audit_models(hard, soft, old_predictions, old_training, model_dir):
    """Reconstruct every frozen feature, prediction, fitted coefficient and payout."""
    model_dir = Path(model_dir)
    rows = read(model_dir/'predictions.jsonl.gz')
    training = read(model_dir/'train_features.jsonl.gz')
    manifest = json.loads((model_dir/'manifest.json').read_text(encoding='utf8'))
    results = json.loads((model_dir/'results.json').read_text(encoding='utf8'))
    old_map = {r['id']: r for r in old_predictions}
    if len(rows) != 1260 or len({r['id'] for r in rows}) != 1260 or set(old_map) != {r['id'] for r in rows}:
        raise ValueError('evaluation_cohort_changed')
    inputs = {r['id']: r for r in old_training+old_predictions}
    refs = {}
    max_probability_error = max_detail_error = max_points_error = 0.
    for identifier, row in inputs.items():
        hr = reference(hard, row)
        sr = reference(soft, row)
        if hr['history_game_ids'] != row['history_game_ids'] or sr['history_game_ids'] != row['history_game_ids']:
            raise ValueError('history_changed')
        if hr['asof_prior_games'] != row['asof_prior_games']:
            raise ValueError('prior_window_changed')
        q = score_probability(hr['means']['nonspatial'])
        expected_q = row.get('nonspatial', row.get('probabilities', {}).get('nonspatial'))
        if abs(q-expected_q) > 1e-10:
            raise ValueError('q_not_reproduced')
        refs[identifier] = dict(nonspatial=q, hard=score_probability(hr['means']['conserved']),
                                soft=score_probability(sr['means']['conserved']), contexts=dict(hard=hr,soft=sr))
    def check_features(row):
        nonlocal max_probability_error, max_detail_error, max_points_error
        ref = refs[row['id']]
        for kind in ('hard','soft'):
            error = abs(row['probabilities'][kind]-ref[kind])
            max_probability_error = max(error,max_probability_error)
            if error > 1e-10:
                raise ValueError('candidate_probability')
            expected_delta = logit(ref[kind])-logit(ref['nonspatial'])
            if abs(row['feature_delta'][kind]-expected_delta) > 1e-9:
                raise ValueError('candidate_delta')
            ctx = ref['contexts'][kind]
            for stored_side, computed_side in zip(row['family_details'][kind],ctx['components'],strict=True):
                for stored, computed in zip(stored_side,computed_side,strict=True):
                    errors = [abs(stored['family_rate']-computed['q_rate'])]
                    errors += [abs(a-b) for a,b in zip(stored['weights'],computed['weights'],strict=True)]
                    errors += [abs(a-b) for a,b in zip(stored['accuracy'],computed['accuracy'],strict=True)]
                    error = max(errors)
                    max_detail_error = max(error,max_detail_error)
                    if error > 1e-10 or abs(sum(stored['weights'])-1) > 1e-12:
                        raise ValueError('family_details_not_reproduced')
            if 'expected_points' in row:
                points = [sum(x*w for x,w in zip(side,(2,3,1))) for side in ctx['means']['conserved']]
                error = max(abs(a-b) for a,b in zip(points,row['expected_points'][kind]))
                max_points_error = max(error,max_points_error)
                if error > 1e-9:
                    raise ValueError('candidate_points')
    for row in rows:
        old = old_map[row['id']]
        if any(row[k] != old[k] for k in ('id','espn_id','day','period','home_win','odds','teams','scores','history_game_ids','posthoc_target_in_source_pool')):
            raise ValueError('evaluation_identity_or_price_changed')
        for name, prior_name in [('q','nonspatial'),('old_s','spatial'),('old_residual','spatial_residual'),('market','market'),('prior_outcome_form','prior_outcome_form')]:
            if row['probabilities'][name] != old['probabilities'][prior_name]:
                raise ValueError('frozen_control_changed')
        check_features(row)
        for name,p in row['probabilities'].items():
            expected = dict(side='home' if p >= .5 else 'away',odds=row['odds'][int(p<.5)])
            if row['selections'][name] != expected:
                raise ValueError('selection_or_price_mismatch')
    old_train_map = {(r['beta_state_id'],r['id']): r for r in old_training}
    if len(training) != 1908 or {(r['beta_state_id'],r['id']) for r in training} != set(old_train_map):
        raise ValueError('training_cohort_changed')
    for row in training:
        old = old_train_map[row['beta_state_id'],row['id']]
        if any(row[k] != old[k] for k in ('id','espn_id','day','teams','odds','home_win','market','history_game_ids')):
            raise ValueError('training_identity_changed')
        check_features(row)
    beta_errors = {}
    for phase, n, cutoff in [('A',721,'2025-08-28'),('B',1187,'2025-12-28')]:
        selected = [r for r in training if r['beta_state_id']==phase]
        if len(selected) != n or max(r['day'] for r in selected) > cutoff:
            raise ValueError('training_cutoff_changed')
        for kind in ('hard','soft'):
            independent = [dict(market=r['market'],home_win=r['home_win'],nonspatial=refs[r['id']]['nonspatial'],
                                **{kind:refs[r['id']][kind]}) for r in selected]
            beta = beta_root(independent,kind)
            stored = manifest['states'][phase][kind]['beta']
            beta_errors[phase+'_'+kind] = abs(beta-stored)
            if abs(beta-stored) > 1e-6:
                raise ValueError('beta_fit_changed')
            for row in rows:
                if row['period']==phase:
                    expected = sigmoid(logit(row['probabilities']['market'])+beta*row['feature_delta'][kind])
                    if abs(expected-row['probabilities'][kind+'_anchor']) > 1e-8:
                        raise ValueError('anchor_not_reproduced')
    checked_metrics = {}
    for phase in ('A','B','all'):
        selected = [r for r in rows if phase=='all' or r['period']==phase]
        checked_metrics[phase] = {m:metrics(selected,m) for m in rows[0]['probabilities']}
        if phase != 'all':
            for model, actual in checked_metrics[phase].items():
                for key,value in actual.items():
                    if abs(value-results[phase]['models'][model][key]) > 1e-9:
                        raise ValueError('reported_metric_mismatch')
    joint = {}
    for phase in ('A','B'):
        values = checked_metrics[phase]
        joint[phase] = {candidate:{control:values[candidate]['hit_rate']>values[control]['hit_rate'] and
                                  values[candidate]['roi']>values[control]['roi']
                                  for control in ('q','old_s','market','prior_outcome_form')}
                        for candidate in ('hard','soft','hard_anchor','soft_anchor')}
    return dict(evaluation_rows=len(rows),training_rows=len(training),unique_feature_keys=len(refs),
                max_probability_error=max_probability_error,max_detail_error=max_detail_error,
                max_expected_points_error=max_points_error,beta_errors=beta_errors,
                metrics=checked_metrics,joint_hit_roi_improvement=joint)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--old-predictions', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--soft',type=Path)
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--old-training',type=Path)
    parser.add_argument('--model-dir',type=Path)
    args = parser.parse_args()
    summary, rows = diagnosis(read(args.pool), read(args.old_predictions))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'independent-volume-diagnosis.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf8')
    with gzip.GzipFile(filename=str(args.out/'independent-volume-components.jsonl.gz'), mode='wb', mtime=0) as handle:
        handle.write(('\n'.join(json.dumps(r, sort_keys=True) for r in rows)+'\n').encode())
    print(json.dumps(summary))
    if args.soft:
        soft = read(args.soft)
        if args.cache:
            result = verify_soft(read(args.pool),soft,args.cache)
            (args.out/'independent-soft-recount.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
        if args.model_dir:
            result = audit_models(read(args.pool),soft,read(args.old_predictions),read(args.old_training),args.model_dir)
            (args.out/'independent-model-audit.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
            print(json.dumps({k:v for k,v in result.items() if k != 'metrics'}))


if __name__ == '__main__':
    main()
