"""Study E: offline, fixed-offset residuals; no production imports or network."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = ROOT / 'docs/research/2026-10-30-shrunk-market-residual-protocol.json'
LEAGUES = ['KBO', 'NPB', 'MLB']
FEATURES = ['rest_gap', 'history_strength', 'kbo_starter_pitches',
            'kbo_relief_appearances', 'npb_relief_pitches', 'mlb_heavy_relievers',
            'present_KBO', 'present_NPB', 'present_MLB']
BASELINES = ['market', 'fixed9:1', 'proportional_market']
FAMILY = 60


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def save(path, obj, lines=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(canonical(x) + '\n' for x in obj) if lines else canonical(obj) + '\n'
    if path.suffix == '.gz':
        path.write_bytes(gzip.compress(text.encode(), mtime=0))
    else:
        path.write_text(text, encoding='utf-8', newline='\n')


def load(name):
    with gzip.open(HERE / 'inputs' / (name + '.jsonl.gz'), 'rt', encoding='utf-8') as file:
        return [json.loads(line) for line in file]


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.utcoffset() is None:
        raise ValueError('timezone required')
    return dt.astimezone(timezone.utc)


def field_gate(value, meta, case, strict=False):
    """Actual receipt clocks never authenticated just by being parseable."""
    try:
        if not math.isfinite(float(value)) or not isinstance(meta, dict):
            return False, 'invalid_value_or_metadata'
        decision = stamp(case['decision_at'])
        observed = stamp(meta['observed_at'])
        hashes = meta['source_sha256']
        hashes = [hashes] if isinstance(hashes, str) else hashes
        if not hashes or not all(isinstance(h, str) and len(h) == 64 and
                                 all(c in '0123456789abcdef' for c in h) for h in hashes):
            return False, 'invalid_source_hash'
        clean_id = lambda v: str(v).removeprefix('NPB:').removeprefix('KBO:').removeprefix('MLB:')
        if clean_id(case['event_id']) in [clean_id(v) for v in meta.get('source_game_ids', [])]:
            return False, 'target_game_in_source'
        if meta.get('max_fact_time'):
            fact = stamp(meta['max_fact_time'])
        else:
            day = date.fromisoformat(meta['max_fact_date'])
            tz = {'UTC': timezone.utc, 'Asia/Seoul': timezone(timedelta(hours=9)),
                  'Asia/Tokyo': timezone(timedelta(hours=9))}[meta['fact_date_timezone']]
            fact = datetime.combine(day + timedelta(days=2), time(), tz)
        if fact > decision or fact > observed:
            return False, 'late_or_impossible_fact'
        selected = meta.get('selected_fact_dates', [])
        if selected and (max(selected) != meta.get('max_fact_date', max(selected)) or
                         any(d >= case['day'] for d in selected)):
            return False, 'current_or_inconsistent_source_date'
        if strict:
            # No external receipt authority/registry was supplied to this study.
            return False, 'no_independent_receipt_authentication'
        return True, 'retrospective_only'
    except (KeyError, ValueError, TypeError, OverflowError):
        return False, 'missing_or_invalid_evidence'


def target_cases(raw, canonical_targets):
    aliases = defaultdict(dict)
    targets = {r['fixture_id']: r for r in canonical_targets}
    for r in raw:
        if r['league'] in LEAGUES and r['event_id'] in targets:
            target = targets[r['event_id']]
            for side in ('home', 'away'):
                old = aliases[r['league']].setdefault(r[side], target[side])
                assert old == target[side], 'conflicting label-free identity bridge'
    cases, audit, seen = [], Counter(), set()
    for r in raw:
        if r['league'] not in LEAGUES or r['cohort'] != 'retrospective_year_strict__claimed_time_only':
            continue
        if r.get('void') or r.get('outcome') not in (0, 1):
            audit['void_or_unsettled_excluded'] += 1
            continue
        if r['sides'] != ['home', 'away'] or r.get('external_p') is None:
            audit['not_binary_actual_vote'] += 1
            continue
        if stamp(r['available_at']) > stamp(r['decision_at']):
            audit['late_case_input'] += 1
            continue
        assert stamp(r['decision_at']) < stamp(r['kickoff'])
        assert len(r['odds']) == 2 and all(math.isfinite(x) and x > 1 for x in r['odds'])
        assert len(r['external_p']) == 2 and abs(sum(r['external_p']) - 1) < 1e-10
        assert all(0 <= p <= 1 for p in r['external_p'])
        assert len(r['market_p']) == 2 and abs(sum(r['market_p']) - 1) < 1e-10
        row = {k: r[k] for k in ['league', 'cohort', 'event_id', 'kickoff', 'decision_at',
                                   'available_at', 'day', 'home', 'away', 'odds', 'outcome',
                                   'market_p', 'external_p', 'price_evidence']}
        for side in ('home', 'away'):
            row['team_' + side] = aliases[r['league']][r[side]]
        identity = (row['league'], stamp(row['kickoff']), row['team_home'], row['team_away'])
        assert identity not in seen, 'physical target alias ambiguity'
        seen.add(identity)
        inv = 1 / np.asarray(row['odds'], dtype=float)
        row['offset_p'] = float(inv[0] / inv.sum())
        row['offset'] = math.log(row['offset_p'] / (1 - row['offset_p']))
        cases.append(row)
    return sorted(cases, key=lambda r: (r['day'], r['kickoff'], r['league'], r['event_id'])), dict(audit), dict(aliases)


def chronological(cases):
    days = sorted({r['day'] for r in cases})
    val_day, test_day = days[int(.6 * len(days))], days[int(.8 * len(days))]
    val_decision = min(stamp(r['decision_at']) for r in cases if r['day'] == val_day)
    train = [r for r in cases if r['day'] < val_day and stamp(r['kickoff']) < val_decision - timedelta(hours=48)]
    validation = [r for r in cases if val_day <= r['day'] < test_day]
    test = [r for r in cases if r['day'] >= test_day]
    embargo = [r['event_id'] for r in cases if r['day'] < val_day and r not in train]
    return train, validation, test, dict(validation_start=val_day, test_start=test_day,
                                      train_freeze_day=max(r['day'] for r in train),
                                      embargo_excluded=embargo, unique_days=len(days))


def history_index(history):
    result = defaultdict(list)
    for r in history:
        if r['league'] == 'MLB':
            assert r.get('game_type') in ['R', 'F', 'D', 'L', 'W']
        h, a = r['hg'], r['ag']
        win = 1. if h > a else 0. if h < a else .5
        for side, w in [('home', win), ('away', 1 - win)]:
            result[r['league'], r[side]].append((r['date'], w))
    for values in result.values():
        values.sort(key=lambda x: x[0])
    return result


def feature_index():
    index = defaultdict(list)
    for name in ['official_phase2', 'official_phase3', 'mlb_calendar']:
        for r in load(name):
            index[r['cohort'], r['event_id'], r['kickoff']].append(r)
    return index


def add_features(cases, history, indexed, freeze):
    audit = Counter()
    hi = history_index(history)
    output = []
    for row in cases:
        r = dict(row)
        target = date.fromisoformat(r['day'])
        limit = min((target - timedelta(days=2)).isoformat(), freeze)
        strengths, rests, counts = [], [], []
        for side in ('home', 'away'):
            prior = hi[r['league'], r['team_' + side]]
            schedule = [d for d, _ in prior if d < r['day']]
            rests.append(min(7., max(0., (target - date.fromisoformat(max(schedule))).days - 1)) if schedule else 0.)
            past = [(d, w) for d, w in prior if d <= limit][-30:]
            counts.append(len(past))
            strengths.append((5 + sum(w for _, w in past)) / (10 + len(past)))
        strength = strengths[0] - strengths[1] if min(counts) >= 10 else 0.
        vals = dict.fromkeys(FEATURES, 0.)
        vals.update(rest_gap=(rests[0] - rests[1]) / 7, history_strength=strength)
        r['history_audit'] = dict(result_cutoff=limit, counts=counts,
                                  schedule_gap_is_retrospective_incomplete=True)
        fields, conflicts = {}, set()
        for feature in indexed.get((r['cohort'], r['event_id'], r['kickoff']), []):
            if stamp(feature['decision_at']) > stamp(r['decision_at']):
                audit['feature_decision_late'] += 1
                continue
            for key, value in feature['features'].items():
                meta = feature.get('feature_evidence', {}).get(key)
                ok, why = field_gate(value, meta, r)
                audit['field:' + why] += 1
                if not ok:
                    continue
                strict, _ = field_gate(value, meta, r, strict=True)
                audit['certified_fields'] += int(strict)
                if key in fields and fields[key][0] != value:
                    conflicts.add(key)
                fields[key] = (value, meta)
        for key in conflicts:
            fields.pop(key, None)
            audit['conflicting_feature_excluded'] += 1
        slots = {'KBO': [('kbo_starter_pitches', 'observed_starter_pitch_count_prior3_source_dates', 100),
                         ('kbo_relief_appearances', 'observed_relief_appearances_prior3_source_dates', 10)],
                 'NPB': [('npb_relief_pitches', 'observed_relief_pitch_count_prior3_source_dates', 100)],
                 'MLB': [('mlb_heavy_relievers', 'relievers_ge30_pitches_lagged_dates_2_4', 3)]}
        used = {}
        for slot, source, divisor in slots[r['league']]:
            hk, ak = 'home_' + source, 'away_' + source
            if hk in fields and ak in fields:
                vals[slot] = (fields[hk][0] - fields[ak][0]) / divisor
                used[hk], used[ak] = fields[hk], fields[ak]
        vals['present_' + r['league']] = float(bool(used))
        audit['workload_present:' + r['league']] += int(bool(used))
        audit['missing_history:' + r['league']] += int(min(counts) < 10)
        r['features'], r['used_workload_evidence'] = vals, used
        output.append(r)
    return output, dict(audit)


def preprocess(train, feature_set):
    names = [] if feature_set == 'intercept' else FEATURES[:2] if feature_set == 'core' else FEATURES
    x = np.array([[r['features'][name] for name in names] for r in train], dtype=float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-8] = 1.
    return dict(names=names, means=means.tolist(), scales=scales.tolist())


def matrix(rows, prep):
    x = np.array([[r['features'][name] for name in prep['names']] for r in rows], dtype=float)
    return np.column_stack([np.ones(len(rows)), (x - prep['means']) / prep['scales']])


def design(x, leagues, architecture, supported):
    blocks = ([x] if architecture == 'partial' else [])
    blocks += [x * np.asarray([l == league for l in leagues])[:, None] for league in supported]
    return np.concatenate(blocks, axis=1)


def objective(theta, z, y, offset, penalties):
    logits = offset + z @ theta
    value = np.logaddexp(0, logits).sum() - y @ logits + .5 * np.sum(penalties * theta ** 2)
    grad = z.T @ (expit(logits) - y) + penalties * theta
    return float(value), grad


def fit(train, candidate, minimum=30):
    supported = [l for l in LEAGUES if sum(r['league'] == l for r in train) >= minimum
                 and len({r['outcome'] for r in train if r['league'] == l}) == 2]
    selected = [r for r in train if r['league'] in supported]
    if not selected or (candidate['architecture'] == 'partial' and len(supported) < 2):
        return dict(candidate=candidate, unavailable=True, supported=[])
    prep = preprocess(selected, candidate['features'])
    x = matrix(selected, prep)
    z = design(x, [r['league'] for r in selected], candidate['architecture'], supported)
    width = x.shape[1]
    penalties = ([candidate['l2_global']] * width if candidate['architecture'] == 'partial' else [])
    penalties += [candidate.get('l2_deviation', candidate.get('l2'))] * (width * len(supported))
    penalties = np.asarray(penalties, dtype=float)
    y = np.array([r['outcome'] == 0 for r in selected], dtype=float)
    offset = np.array([r['offset'] for r in selected])
    result = minimize(objective, np.zeros(z.shape[1]), args=(z, y, offset, penalties),
                      jac=True, method='L-BFGS-B', options=dict(maxiter=2000, ftol=1e-13, gtol=1e-8))
    if not result.success:
        raise RuntimeError((candidate, result.message))
    return dict(candidate=candidate, prep=prep, supported=supported, theta=result.x.tolist(),
                penalty=penalties.tolist(), objective=float(result.fun), iterations=result.nit,
                max_abs_gradient=float(np.max(np.abs(result.jac))), n=len(selected))


def predict(rows, model):
    if model.get('unavailable'):
        return [r['offset_p'] for r in rows]
    x = matrix(rows, model['prep'])
    z = design(x, [r['league'] for r in rows], model['candidate']['architecture'], model['supported'])
    result = expit(np.array([r['offset'] for r in rows]) + z @ model['theta'])
    return [float(p) if r['league'] in model['supported'] else r['offset_p'] for r, p in zip(rows, result)]


def baseline(rows, name):
    if name == 'market':
        return [r['market_p'][0] for r in rows]
    if name == 'fixed9:1':
        return [.9 * r['market_p'][0] + .1 * r['external_p'][0] for r in rows]
    if name == 'proportional_market':
        return [r['offset_p'] for r in rows]
    if name == 'home':
        return [1. for _ in rows]
    raise ValueError(name)


def event_metrics(rows, probabilities):
    picks = np.array([0 if p >= .5 else 1 for p in probabilities])
    correct = np.array([int(i == r['outcome']) for r, i in zip(rows, picks)])
    # Exact integer cents: binary saved decimal prices have max two places.
    cents = np.array([int(round(r['odds'][i] * 100)) * int(hit) - 100
                      for r, i, hit in zip(rows, picks, correct)], dtype=np.int64)
    return picks, correct, cents


def metrics(rows, probabilities):
    if not rows:
        return dict(n=0, accuracy=None, roi=None)
    picks, correct, cents = event_metrics(rows, probabilities)
    p = np.clip(np.array(probabilities), 1e-15, 1 - 1e-15)
    y = np.array([r['outcome'] == 0 for r in rows], dtype=float)
    return dict(n=len(rows), bets=len(rows), coverage=1., wins=int(correct.sum()),
                accuracy=float(correct.mean()), profit_cents=int(cents.sum()), roi=float(cents.sum() / (100 * len(rows))),
                logloss=round(float(-(y * np.log(p) + (1 - y) * np.log1p(-p)).mean()), 12),
                brier=round(float((2 * (p - y) ** 2).mean()), 12), home_picks=int(sum(picks == 0)),
                away_picks=int(sum(picks == 1)), home_share=float(np.mean(picks == 0)),
                home_constant_agreement=bool(all(picks == 0)))


def holm(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    adjusted, previous = [1.] * len(values), 0.
    for rank, i in enumerate(order):
        previous = max(previous, min(1., (len(values) - rank) * values[i]))
        adjusted[i] = previous
    return adjusted


def weekly(rows, p, q):
    _, pc, pr = event_metrics(rows, p)
    _, qc, qr = event_metrics(rows, q)
    weeks = sorted({date.fromisoformat(r['day']).isocalendar()[:2] for r in rows})
    groups = [[i for i, r in enumerate(rows) if date.fromisoformat(r['day']).isocalendar()[:2] == w] for w in weeks]
    sums = np.array([[len(g), (pc[g] - qc[g]).sum(), (pr[g] - qr[g]).sum()] for g in groups], dtype=np.int64)
    rng = np.random.default_rng(20261030)
    sampled = sums[rng.integers(0, len(weeks), size=(5000, len(weeks)))].sum(axis=1)
    values = sampled[:, 1:] / sampled[:, :1]
    values[:, 1] /= 100
    regular = np.quantile(values, [.025, .975], axis=0)
    simultaneous = np.quantile(values, [.05 / FAMILY / 2, 1 - .05 / FAMILY / 2], axis=0)
    improved = int(sum((pc == 1) & (qc == 0)))
    worsened = int(sum((pc == 0) & (qc == 1)))
    pvalue = float(binomtest(improved, improved + worsened, .5, alternative='greater').pvalue) if improved + worsened else 1.
    return dict(weeks=len(weeks), accuracy_delta=float((pc - qc).mean()),
                roi_delta=float((pr - qr).sum() / (100 * len(rows))),
                accuracy_ci95=np.round(regular[:, 0], 12).tolist(), roi_ci95=np.round(regular[:, 1], 12).tolist(),
                accuracy_family_ci=np.round(simultaneous[:, 0], 12).tolist(), roi_family_ci=np.round(simultaneous[:, 1], 12).tolist(),
                discordant_improved=improved, discordant_worsened=worsened, p_raw=pvalue,
                tail_draws_expected=5000 * .05 / FAMILY / 2)


def flips(rows, p, q):
    pi, pc, _ = event_metrics(rows, p)
    qi, qc, _ = event_metrics(rows, q)
    mask = pi != qi
    return dict(n=int(mask.sum()), wins=int(pc[mask].sum()), baseline_wins=int(qc[mask].sum()),
                accuracy=float(pc[mask].mean()) if any(mask) else None,
                baseline_accuracy=float(qc[mask].mean()) if any(mask) else None)


def effective_coefficients(model, league):
    if model.get('unavailable') or league not in model['supported']:
        return None
    width = 1 + len(model['prep']['names'])
    blocks = np.array(model['theta']).reshape(-1, width)
    idx = model['supported'].index(league)
    effective = blocks[0] + blocks[idx + 1] if model['candidate']['architecture'] == 'partial' else blocks[idx]
    # Recover coefficients on original feature units, including original intercept.
    slopes = effective[1:] / model['prep']['scales']
    intercept = effective[0] - slopes @ model['prep']['means']
    return dict(zip(['intercept'] + model['prep']['names'], [float(intercept)] + slopes.tolist()))


def stability(train, candidate):
    days = sorted({r['day'] for r in train})
    mid = days[len(days) // 2]
    early, late = [r for r in train if r['day'] < mid], [r for r in train if r['day'] >= mid]
    models = [fit(rs, candidate, minimum=15) for rs in [early, late]]
    output = dict(midpoint=mid, models=models, leagues={})
    for league in LEAGUES:
        coefs = [effective_coefficients(m, league) for m in models]
        entry = dict(early=coefs[0], late=coefs[1])
        if all(c is not None for c in coefs):
            a, b = [np.array(list(c.values())) for c in coefs]
            entry.update(cosine=float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
                         if np.linalg.norm(a) * np.linalg.norm(b) else None,
                         sign_changed=[key for key in coefs[0] if coefs[0][key] * coefs[1][key] < 0])
        output['leagues'][league] = entry
    return output


def run(raw=None, history=None, index=None, stability_enabled=True):
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    cases, audit, aliases = target_cases(load('cases') if raw is None else raw, load('canonical_targets'))
    train0, val0, test0, split = chronological(cases)
    featured, faudit = add_features(cases, load('history') if history is None else history,
                                   feature_index() if index is None else index, split['train_freeze_day'])
    fmap = {r['event_id']: r for r in featured}
    train, val, test = [[fmap[r['event_id']] for r in group] for group in [train0, val0, test0]]
    assert len(val) >= protocol['chronology']['min_validation_global']
    models, validation, predictions = {}, {}, {}
    for candidate in protocol['candidates']:
        name = candidate['id']
        models[name] = fit(train, candidate)
        vp = predict(val, models[name])
        validation[name] = metrics(val, vp)
        predictions[name] = predict(test, models[name])
    selected = min(validation, key=lambda k: (-validation[k]['accuracy'], validation[k]['logloss'], k))
    for name in BASELINES + ['home']:
        predictions[name] = baseline(test, name)
    summaries, contrasts, refs = [], [], []
    for league in LEAGUES:
        idx = [i for i, r in enumerate(test) if r['league'] == league]
        rows = [test[i] for i in idx]
        for name, probs in predictions.items():
            p = [probs[i] for i in idx]
            summary = dict(league=league, model=name, selected=name == selected, metrics=metrics(rows, p))
            if name in models:
                summary['fitted_coverage'] = float(league in models[name]['supported'])
                summary['flip_vs_market'] = flips(rows, p, [predictions['market'][i] for i in idx])
                summary['flip_vs_proportional'] = flips(rows, p, [predictions['proportional_market'][i] for i in idx])
                summary['effective_coefficients'] = effective_coefficients(models[name], league)
                for base in BASELINES:
                    contrast = dict(league=league, model=name, baseline=base,
                                    **weekly(rows, p, [predictions[base][i] for i in idx]))
                    contrasts.append(contrast)
                    if base in BASELINES[:2]:
                        refs.append(contrast)
            summaries.append(summary)
    assert len(refs) == FAMILY
    for result, adjusted in zip(refs, holm([r['p_raw'] for r in refs])):
        result['p_holm'] = adjusted
    selected_flips = flips(test, predictions[selected], predictions['market'])
    conclusions = {}
    for league in LEAGUES:
        required = [r for r in refs if r['league'] == league and r['model'] == selected]
        sm = next(r for r in summaries if r['league'] == league and r['model'] == selected)
        conclusions[league] = bool(sm['metrics']['n'] >= 10 and sm['flip_vs_market']['n'] >= 10 and
                                   selected_flips['n'] >= 20 and
                                   all(r['p_holm'] < .05 and r['accuracy_family_ci'][0] > 0 for r in required))
    details = [dict(event_id=r['event_id'], league=r['league'], day=r['day'], outcome=r['outcome'], odds=r['odds'],
                    split='test', probabilities={name: ps[i] for name, ps in predictions.items()},
                    settlements={name: dict(pick=int(event_metrics([r], [ps[i]])[0][0]),
                                            correct=int(event_metrics([r], [ps[i]])[1][0]),
                                            profit_cents=int(event_metrics([r], [ps[i]])[2][0]))
                                 for name, ps in predictions.items()}) for i, r in enumerate(test)]
    return dict(protocol_sha256=sha(PROTOCOL), target_audit=audit, feature_audit=faudit, aliases=aliases,
                split=split, split_counts={label: dict(Counter(r['league'] for r in rs))
                                           for label, rs in [('train', train), ('validation', val), ('test', test)]},
                selected=selected, validation=validation, models=models, summaries=summaries, contrasts=contrasts,
                overall={name: metrics(test, probabilities) for name, probabilities in predictions.items()},
                selected_flips=selected_flips, improvement=conclusions, predictions=details,
                featured=featured, stability=stability(train, models[selected]['candidate']) if stability_enabled else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'results')
    args = parser.parse_args()
    provenance = json.loads((HERE / 'provenance.json').read_text(encoding='utf-8'))
    for source in provenance:
        assert sha(HERE / source['input_path']) == source['sha256'], source['name']
    result = run()
    for field in ['models', 'predictions', 'featured', 'stability']:
        obj = result.pop(field)
        save(args.output / (field + ('.jsonl.gz' if field in ['predictions', 'featured'] else '.json')),
             obj, lines=field in ['predictions', 'featured'])
    save(args.output / 'results.json', result)
    print(canonical(dict(selected=result['selected'], split_counts=result['split_counts'],
                         selected_flips=result['selected_flips'], improvement=result['improvement'])))


if __name__ == '__main__':
    main()
