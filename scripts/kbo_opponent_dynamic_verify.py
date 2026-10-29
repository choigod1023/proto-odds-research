"""Reproduce committed models and emit compact audit + contribution diagnostics."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import sys
import time
import unittest

import numpy as np
from scipy.special import logit

sys.dont_write_bytecode = True
import kbo_opponent_dynamic_research as m


def verify(folder):
    start = time.perf_counter()
    frozen = json.loads((folder/'frozen-selection.json').read_text(encoding='utf8'))
    if m.input_hashes(folder) != frozen['input_sha256']:
        raise ValueError('frozen_code_or_input_hash_changed')
    cohort, games, audit = m.load_facts(folder)
    rows, snapshots = m.replay(cohort, games, True)
    evaluation = [r for r in rows if r['role'] == 'evaluation']
    fresh, _ = m.freeze_from_rows(rows)
    if fresh['heads'] != frozen['heads']:
        raise ValueError('selection_not_reproduced')
    diagnostics = {}
    predictions = {}
    for name, c in frozen['candidates'].items():
        for stage in ['selection_fit', 'final_fit']:
            for field in ['beta', 'scale']:
                if field in c[stage]:
                    np.testing.assert_allclose(fresh['candidates'][name][stage][field], c[stage][field], atol=1e-12, rtol=0)
        predictions[name] = m.predict(c['final_fit'], evaluation)
        config = c['final_fit']
        info = dict(training_years=config['training_years'])
        if config['kind'] in {'residual', 'blend'}:
            x = np.array([r['dynamic'][config['state']]['x'] for r in evaluation])
            delta = logit(predictions[name])-logit(predictions['baseline'])
            info.update(feature_min=x.min(axis=0).tolist(), feature_max=x.max(axis=0).tolist(),
                feature_nonzero_rows=int(np.any(np.abs(x) > 1e-12, axis=1).sum()),
                logit_contribution_min=float(delta.min()), logit_contribution_max=float(delta.max()),
                probability_delta_mean=float(np.mean(predictions[name]-predictions['baseline'])),
                probability_delta_max_abs=float(np.max(np.abs(predictions[name]-predictions['baseline']))),
                changes_vs_baseline=int(np.sum((predictions[name] >= .5) != (predictions['baseline'] >= .5))))
            if config['kind'] == 'residual':
                info.update(beta=config['beta'], scale=config['scale'])
        diagnostics[name] = info
    primary = frozen['heads']['primary']
    changes = []
    for r, p, b in zip(evaluation, predictions[primary], predictions['baseline']):
        if (p >= .5) == (b >= .5):
            continue
        changes.append({k: r[k] for k in ['date', 'home', 'away', 'hg', 'ag']} |
                       dict(primary_probability=float(p), baseline_probability=float(b),
                            primary_side='home' if p >= .5 else 'away',
                            baseline_side='home' if b >= .5 else 'away',
                            primary_profit_krw=m.settlement(r, p)[1], baseline_profit_krw=m.settlement(r, b)[1],
                            state=r['dynamic'][frozen['candidates'][primary]['specification']['state']]))
    # Tests cover independent Shin, price settlement, causal perturbation, snapshots and all ledgers.
    suite = unittest.defaultTestLoader.discover(str(m.ROOT/'tests'), pattern='test_kbo_opponent_dynamic_research.py')
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful() or result.skipped:
        raise ValueError('verification_tests_failed_or_skipped')
    output = dict(status='passed', tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), float_tolerance=dict(atol=1e-12, rtol=0),
                  exact_checks=['decisions', 'integer profit', 'cohort identity', 'source game IDs'],
                  protocol_commit=frozen['protocol_commit'], freeze_commit='7d3ee091',
                  frozen_selection_sha256=m.normalized_hash(folder/'frozen-selection.json'),
                  result_sha256=m.normalized_hash(folder/'results.json'),
                  source_audit=audit, state_snapshot_records=len(snapshots),
                  evaluation_month_counts=dict(Counter(r['date'][:7] for r in evaluation)),
                  diagnostics=diagnostics, primary_changed_picks=changes,
                  python=platform.python_version(), numpy=np.__version__, scipy=m.scipy.__version__,
                  elapsed_seconds=time.perf_counter()-start)
    m.write_json(folder/'verification.json', output)
    print(json.dumps({k: output[k] for k in ['status', 'tests_run', 'failures', 'errors', 'skipped', 'state_snapshot_records']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=m.DEFAULT)
    args = parser.parse_args()
    # Unit tests intentionally use the same requested committed directory.
    m.DEFAULT = args.folder
    if args.folder.resolve() != (m.ROOT/'experiments/baseball-information-stepchange-20261029/kbo').resolve():
        parser.error('tests verify the repository committed artifact directory')
    verify(args.folder)
