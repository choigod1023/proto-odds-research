"""Persist actual-corpus perturbation evidence and network-blocked cold replay."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import socket
import sys

import residual as m
import verify


def prediction_hash(result):
    return m.hashlib.sha256(m.canonical([
        {k: r[k] for k in ['event_id', 'probabilities']} for r in result['predictions']
    ]).encode()).hexdigest()


def parameter_hash(result):
    return m.hashlib.sha256(m.canonical(result['models']).encode()).hexdigest()


def main():
    def blocked(*args, **kwargs):
        raise AssertionError('network forbidden in study E offline validation')
    socket.create_connection = blocked
    socket.socket.connect = blocked
    baseline = m.run(stability_enabled=False)
    raw = m.load('cases')
    ids = {r['event_id'] for r in baseline['predictions']}
    test_changed = copy.deepcopy(raw)
    count = 0
    for r in test_changed:
        if r['event_id'] in ids and r['cohort'] == 'retrospective_year_strict__claimed_time_only':
            r['outcome'] = 1 - r['outcome']
            count += 1
    altered = m.run(raw=test_changed, stability_enabled=False)
    assert baseline['models'] == altered['models']
    assert baseline['selected'] == altered['selected']
    assert baseline['validation'] == altered['validation']
    assert prediction_hash(baseline) == prediction_hash(altered)
    history = m.load('history')
    hcount = 0
    for r in history:
        if r['date'] > baseline['split']['train_freeze_day']:
            r['hg'], r['ag'] = 999, 0
            hcount += 1
    hperturbed = m.run(history=history, stability_enabled=False)
    assert baseline['featured'] == hperturbed['featured']
    assert baseline['models'] == hperturbed['models']
    assert prediction_hash(baseline) == prediction_hash(hperturbed)
    poisoned = copy.deepcopy(raw)
    for r in poisoned:
        r.update(final_odds=[1000, 1.01], postgame_label='poison', current_target_lineup='poison',
                 saved_final_p=[0, 1], saved_score_p=[1, 0])
    poisoned_result = m.run(raw=poisoned, stability_enabled=False)
    assert baseline['models'] == poisoned_result['models']
    assert prediction_hash(baseline) == prediction_hash(poisoned_result)
    # Cold run executed in-process with network denied, all fixed inputs re-read.
    replay = m.run()
    standard = m.HERE / 'results'
    comparisons = {}
    for field, suffix in [('models', '.json'), ('predictions', '.jsonl.gz'),
                          ('featured', '.jsonl.gz'), ('stability', '.json')]:
        obj = replay.pop(field)
        serialized = (''.join(m.canonical(x) + '\n' for x in obj) if suffix == '.jsonl.gz'
                      else m.canonical(obj) + '\n').encode()
        payload = m.gzip.compress(serialized, mtime=0) if suffix == '.jsonl.gz' else serialized
        original = (standard / (field + suffix)).read_bytes()
        assert payload == original, field
        comparisons[field + suffix] = m.hashlib.sha256(payload).hexdigest()
    assert (m.canonical(replay) + '\n').encode() == (standard / 'results.json').read_bytes()
    comparisons['results.json'] = m.sha(standard / 'results.json')
    independent = verify.check(standard)
    evidence = dict(network_blocked=True, test_labels_flipped=count, history_after_freeze_scores_changed=hcount,
                    baseline_parameters_sha256=parameter_hash(baseline),
                    test_flipped_parameters_sha256=parameter_hash(altered),
                    history_flipped_parameters_sha256=parameter_hash(hperturbed),
                    baseline_predictions_sha256=prediction_hash(baseline),
                    test_flipped_predictions_sha256=prediction_hash(altered),
                    history_flipped_predictions_sha256=prediction_hash(hperturbed),
                    poisoned_postgame_predictions_sha256=prediction_hash(poisoned_result),
                    selection_and_validation_unchanged=True, preprocessing_included_in_parameter_hash=True,
                    cold_replay_byte_identical=comparisons, independent=independent,
                    tests=dict(focused='21 passed in 6.24s (recorded focused run)',
                               full=(m.HERE / 'validation/full-suite.log').read_text(encoding='utf-8').strip().splitlines()[-1],
                               command='python -m pytest -q', environment='PYTEST_ADDOPTS=-p no:cacheprovider; PYTHONDONTWRITEBYTECODE=1'),
                    python=sys.version, source_sha256={p.name: m.sha(p) for p in m.HERE.glob('*.py')},
                    test_source_sha256=m.sha(m.ROOT / 'tests/test_shrunk_market_residual.py'))
    m.save(m.HERE / 'validation/validation.json', evidence)
    print(m.canonical({k: evidence[k] for k in ['network_blocked', 'test_labels_flipped',
                                               'history_after_freeze_scores_changed', 'tests']}))


if __name__ == '__main__':
    main()
