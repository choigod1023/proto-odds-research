"""Regression for a source publication between the decision and kickoff."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'experiments/dynamic-score-state-model-20261030'
spec = importlib.util.spec_from_file_location('accuracy_integration_prepare', P / 'prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def frame(publication='2026-10-30T09:20:00Z'):
    return {'kickoff': '2026-10-30T10:00:00+00:00',
            'raw_forecasts': [{'source_reported_published_at': publication}]}


@pytest.mark.parametrize('publication,expected', [
    ('2026-10-30T09:20:00Z', None),
    ('2026-10-30T09:30:00Z', None),
    ('2026-10-30T18:30:00+09:00', None),
    ('2026-10-30T09:30:00.000001Z', 'postdecision_publication'),
    ('2026-10-30T09:59:00Z', 'postdecision_publication'),
    ('2026-10-30T10:00:00Z', 'postkickoff_publication'),
    ('2026-10-30T09:20:00', 'unknown_or_naive_publication'),
    ('bad-date', 'unknown_or_naive_publication'),
])
def test_claim_must_exist_by_decision(publication, expected):
    assert prepare.publication_rejection(frame(publication), '2026-10-30T09:30:00Z',
                                         '2026-10-30T10:00:00Z') == expected


@pytest.mark.parametrize('change,expected', [
    ('missing', 'missing_frame'), ('empty', 'unknown_or_naive_publication'),
    ('mismatch', 'frame_kickoff_mismatch'), ('late_vote', 'postdecision_publication'),
    ('naive_decision', 'unknown_or_naive_publication'),
])
def test_inconsistent_frames_fail_closed(change, expected):
    f = frame()
    decision = '2026-10-30T09:30:00Z'
    if change == 'missing': f = None
    if change == 'empty': f['raw_forecasts'] = []
    if change == 'mismatch': f['kickoff'] = '2026-10-30T11:00:00Z'
    if change == 'late_vote': f['raw_forecasts'].append(frame('2026-10-30T09:31:00Z')['raw_forecasts'][0])
    if change == 'naive_decision': decision = '2026-10-30T09:30:00'
    assert prepare.publication_rejection(f, decision, '2026-10-30T10:00:00Z') == expected


def test_saved_actual_vote_inputs_unchanged_and_late_claim_is_rejected():
    with gzip.open(P / 'inputs/targets.jsonl.gz', 'rt', encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    actual = [row for row in rows if row['actual_votes'] and row['kickoff'].startswith('2026-')]
    assert len(actual) == 445
    for row in actual:
        f = {'kickoff': row['kickoff'], 'raw_forecasts': row['publication_evidence']}
        assert prepare.publication_rejection(f, row['decision_at'], row['kickoff']) is None
    fixture = json.loads((ROOT / 'experiments/accuracy-review-20261030/late-publication-fixture.json').read_text(encoding='utf-8-sig'))
    f = frame(fixture['source_reported_published_at'])
    f['kickoff'] = fixture['kickoff']
    assert prepare.publication_rejection(f, fixture['decision_at'], fixture['kickoff']) == 'postdecision_publication'
