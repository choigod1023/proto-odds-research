"""Frozen local initialization/adaptation factorial; no production integration."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import local_gap_information as gap
np, base, info = gap.np, gap.base, gap.info
FAMILIES = ('zero_constant', 'prior_constant', 'zero_fast', 'prior_fast')
PROTOCOL = ROOT/'docs/research/2026-10-01-gap-cold-start-protocol.md'
PROTOCOL_COMMIT = 'e2f608dc'
REFERENCE = ROOT/'docs/research/2026-10-01-gap-information-results.json'


def verify_protocol():
    committed = subprocess.check_output(['git', 'show', f'{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(ROOT).as_posix()}'], cwd=ROOT)
    if info.lf(committed) != info.lf(PROTOCOL.read_bytes()):
        raise ValueError('Frozen cold-start protocol changed')
    return subprocess.check_output(['git', 'rev-parse', PROTOCOL_COMMIT], cwd=ROOT, text=True).strip()


def feature_rows(rows, alpha, phi, family):
    if family not in FAMILIES:
        raise ValueError('Unknown family')
    ordered = sorted(rows, key=lambda r: (r['date'], r['match_id']))
    if len({r['match_id'] for r in ordered}) != len(ordered):
        raise ValueError('Duplicate match identity')
    states, last_season, entrant = {}, {}, {}
    counts = defaultdict(lambda: np.zeros(2, dtype=int))
    totals = defaultdict(lambda: np.zeros((2, 2)))
    samples = defaultdict(lambda: np.zeros(2, dtype=int))
    cursor, features, diagnostics = 0, {}, {}
    for row in ordered:
        cutoff = (date.fromisoformat(row['date'])-timedelta(days=7)).isoformat()
        while cursor < len(ordered) and ordered[cursor]['date'] <= cutoff:
            old = ordered[cursor]
            league = old['league']
            hk, ak = (league, old['home']), (league, old['away'])
            for j, (hkey, akey) in enumerate((('HS', 'AS'), ('HST', 'AST'))):
                sh, sa = old['stats'][hkey], old['stats'][akey]
                if sh is None or sa is None:
                    continue
                ah = max(alpha, .2) if family.endswith('fast') and counts[hk][j] < 6 else alpha
                aa = max(alpha, .2) if family.endswith('fast') and counts[ak][j] < 6 else alpha
                h, a = states[hk][j], states[ak][j]
                nh = gap.update(h, a, sh, sa, ah, phi)[0]
                na = gap.update(h, a, sh, sa, aa, phi)[1]
                states[hk][j], states[ak][j] = nh, na
                counts[hk][j] += 1
                counts[ak][j] += 1
                totals[league][j] += [sh, sa]
                samples[league][j] += 1
            cursor += 1
        league, season = row['league'], row['season']
        team_keys = [(league, row[k]) for k in ('home', 'away')]
        for key in team_keys:
            if key not in states:
                states[key] = np.zeros((2, 4))
                if family.startswith('prior'):
                    for j in range(2):
                        if samples[league][j]:
                            h, a = totals[league][j]/samples[league][j]
                            states[key][j] = [h, a, a, h]
            season_key = (*key, season)
            if season_key not in entrant:
                entrant[season_key] = last_season.get(key) != season-1
                last_season[key] = season
        h, a = [states[key] for key in team_keys]
        features[row['match_id']] = np.array([(h[0, 0]+a[0, 3])/2, (a[0, 2]+h[0, 1])/2,
                                             (h[1, 0]+a[1, 3])/2, (a[1, 2]+h[1, 1])/2])
        diagnostics[row['match_id']] = dict(cold=any(np.any(counts[k] < 6) for k in team_keys),
            entrant=any(entrant[(*k, season)] for k in team_keys), counts=[counts[k].tolist() for k in team_keys])
    return features, diagnostics


def grouped_statistics(rows, features, diagnostics, means):
    groups = {'all': rows,
        'cold': [r for r in rows if diagnostics[r['match_id']]['cold']],
        'entrant': [r for r in rows if diagnostics[r['match_id']]['entrant']],
        'other': [r for r in rows if not diagnostics[r['match_id']]['cold'] and not diagnostics[r['match_id']]['entrant']]}
    result = {}
    for name, part in groups.items():
        metrics = {}
        for j, key in enumerate(gap.KEYS):
            available = [r for r in part if r['stats'][key] is not None]
            values = {}
            for label, predictions in (('gap', features), ('train_mean', {r['match_id']: means[r['league']] for r in available})):
                error = np.array([predictions[r['match_id']][j]-r['stats'][key] for r in available])
                values[label] = dict(mae=float(np.abs(error).mean()) if len(error) else None,
                                     mse=float(np.mean(error**2)) if len(error) else None)
            metrics[key] = dict(observations=len(available), missing=len(part)-len(available), **values)
        result[name] = dict(matches=len(part), statistics=metrics)
    return result


def summarize(rows, forecasts, ledgers):
    result = {}
    for name, p in forecasts.items():
        comparisons = [r for r in ('shin', 'old_gap') if r != name]
        result[name] = dict(metrics=base.match_metrics(rows, p),
            loss_ci={r: gap.rename_difference(base.loss_ci(rows, p, forecasts[r])) for r in comparisons},
            policies={policy: dict(metrics=base.betting_metrics(ledgers[name][policy]),
                ci={r: gap.rename_difference(base.betting_ci(ledgers[name][policy], ledgers[r][policy])) for r in comparisons})
                for policy in base.POLICIES})
    return result


def reference_predictions(rows, fold):
    records = {r['match_id']: r for r in fold['forecasts']}
    if len(records) != len(fold['forecasts']) or set(records) != {r['match_id'] for r in rows}:
        raise ValueError('Reference identity mismatch')
    for row in rows:
        if any(records[row['match_id']][k] != row[k] for k in ('league', 'date', 'y', 'prices')):
            raise ValueError('Reference source values changed')
    return base.validate(np.array([records[r['match_id']]['probabilities']['gap'] for r in rows]))


def run(rows, sources, reference, protocol_commit):
    started = time.perf_counter()
    if sources != reference['source']:
        raise ValueError('Source provenance changed')
    if any(r['league'] not in base.LEAGUES or r['season'] not in range(19, 25) for r in rows):
        raise ValueError('Unexpected fold membership')
    configs = {f'{f}:a{a}:p{p}': (f, a, p) for f in FAMILIES for a, p in gap.GRID}
    feature_sets = {k: feature_rows(rows, a, p, f) for k, (f, a, p) in configs.items()}
    for a, p in gap.GRID:
        old = gap.feature_rows(rows, a, p)
        new = feature_sets[f'zero_constant:a{a}:p{p}'][0]
        if any(not np.array_equal(old[k], new[k]) for k in old):
            raise ValueError('Zero constant feature regression')
    trials, folds, pooled_rows = [], [], []
    pooled_p, pooled_ledgers = defaultdict(list), {}
    fits = 0
    for inner_year, outer_year in ((22, 23), (23, 24)):
        inner_all = [r for r in rows if r['season'] == inner_year]
        outer_all = [r for r in rows if r['season'] == outer_year]
        if max(r['date'] for r in inner_all) >= min(r['date'] for r in outer_all):
            raise ValueError('Temporal overlap')
        train = base.guarded_training(rows, inner_year-1, inner_all)
        refit = base.guarded_training(rows, inner_year, outer_all)
        inner = [r for r in inner_all if r['prices'] is not None]
        outer = [r for r in outer_all if r['prices'] is not None]
        current = []
        for identity, (feature, diagnostic) in feature_sets.items():
            p, audit = gap.fit_predict(train, inner, feature)
            fits += len(audit)
            current.append(dict(id=identity, family=configs[identity][0], inner_year=2000+inner_year,
                metrics=base.match_metrics(inner, p), fit_audit=audit,
                statistics=grouped_statistics(inner_all, feature, diagnostic, gap.mean_statistics(train))))
        trials.extend(current)
        selected = {f: min((t for t in current if t['family'] == f), key=lambda t: (t['metrics']['logloss'], t['id']))['id'] for f in FAMILIES}
        winner = min(current, key=lambda t: (t['metrics']['logloss'], t['id']))['id']
        old_fold = next(f for f in reference['folds'] if f['outer_year'] == outer_year+2000)
        if selected['zero_constant'] != 'zero_constant:'+old_fold['selected_id']:
            raise ValueError('Old GAP inner selection changed')
        modes = {f'selected_{f}': selected[f] for f in FAMILIES}
        modes.update({f'fixed_{f}': f+':'+old_fold['selected_id'] for f in FAMILIES})
        modes['inner_selected'] = winner
        fitted, audits = {}, {}
        for identity in sorted(set(modes.values())):
            fitted[identity], audits[identity] = gap.fit_predict(refit, outer, feature_sets[identity][0])
            fits += len(audits[identity])
        old_p = reference_predictions(outer, old_fold)
        difference = float(np.abs(fitted[selected['zero_constant']]-old_p).max())
        if difference > 1e-10:
            raise ValueError(f'Old GAP probability regression {difference}')
        forecasts = dict(shin=gap.market(outer), old_gap=old_p, **{k: fitted[v] for k, v in modes.items()})
        ledgers = {name: {policy: base.settle(outer_all, base.choose_tickets(outer, p, policy)) for policy in base.POLICIES}
                   for name, p in forecasts.items()}
        means = gap.mean_statistics(refit)
        folds.append(dict(outer_year=2000+outer_year, selected=selected, winner=winner, modes=modes,
            reference_selected_id=old_fold['selected_id'], reproduction_max_abs=difference,
            train_matches=len(train), refit_matches=len(refit), fit_audits=audits,
            train_last_date=max(r['date'] for r in train), refit_last_date=max(r['date'] for r in refit),
            inner_first_date=min(r['date'] for r in inner_all), outer_first_date=min(r['date'] for r in outer_all),
            statistics={k: grouped_statistics(outer_all, *feature_sets[k], means) for k in fitted},
            reports=summarize(outer, forecasts, ledgers), ledgers=ledgers,
            forecasts=[dict(match_id=r['match_id'], league=r['league'], date=r['date'], y=r['y'], prices=r['prices'],
                probabilities={k: p[i].tolist() for k, p in forecasts.items()}) for i, r in enumerate(outer)],
            statistic_forecasts=[dict(match_id=r['match_id'], actual=r['stats'], train_mean=means[r['league']],
                cohorts=feature_sets[next(iter(feature_sets))][1][r['match_id']],
                predicted={k: feature_sets[k][0][r['match_id']].tolist() for k in fitted}) for r in outer_all]))
        pooled_rows.extend(outer)
        for name, p in forecasts.items():
            pooled_p[name].append(p)
            pooled_ledgers.setdefault(name, {policy: [] for policy in base.POLICIES})
            for policy in base.POLICIES:
                pooled_ledgers[name][policy].extend(ledgers[name][policy])
        print(f'Finished fold {outer_year+2000}, inner winner {winner}', flush=True)
    paths = [Path(__file__).resolve(), ROOT/'scripts/local_gap_information.py', ROOT/'scripts/local_lagged_information.py',
             ROOT/'scripts/local_score_model_tournament.py', ROOT/'src/devig.py']
    pooled = summarize(pooled_rows, {k: np.concatenate(v) for k, v in pooled_p.items()}, pooled_ledgers)
    return dict(status='Exploratory exposed historical data; NOT fresh holdout, Proto execution or guaranteed return',
        protocol_commit=protocol_commit, protocol_sha256_lf=gap.digest(PROTOCOL), reference_sha256_lf=gap.digest(REFERENCE),
        code_sha256_lf={p.relative_to(ROOT).as_posix(): gap.digest(p) for p in paths}, source=sources,
        families=FAMILIES, grid=gap.GRID, trial_count=len(trials), actual_league_fits=fits,
        seed=base.SEED, bootstrap_replicates=base.REPS, versions=dict(python=info.platform.python_version(), numpy=np.__version__, scipy=info.scipy.__version__),
        cohort_note='cold and entrant overlap; other excludes both; entrant is observed absence, not proven promotion',
        optimizer_failures=[], runtime_seconds=time.perf_counter()-started, trials=trials, folds=folds,
        pooled=pooled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/research/2026-10-01-gap-cold-start-results.json')
    args = parser.parse_args()
    commit = verify_protocol()
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))
    for path, expected in reference['code_sha256_lf'].items():
        if gap.digest(ROOT/path) != expected:
            raise ValueError(f'Previous dependency changed: {path}')
    rows, sources = info.load_sources()
    result = run(rows, sources, reference, commit)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('trial_count', 'actual_league_fits', 'runtime_seconds')}))
    for name, report in result['pooled'].items():
        print(name, report['metrics'], {k: v['metrics'] for k, v in report['policies'].items()})


if __name__ == '__main__':
    main()
