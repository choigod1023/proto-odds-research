import gzip
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('broad_model_audit', Path(__file__).with_name('audit.py'))
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def write_rows(tmp_path, rows):
    p = tmp_path / 'rows.jsonl.gz'
    with gzip.open(p, 'wt', encoding='utf8') as f:
        for row in rows:
            f.write(json.dumps(row)+'\n')
    return [{'family': 'example', 'path': str(p), 'pr': None}]


def row(model='market'):
    return {'event_id': 'e', 'day': '2026-01-01', 'sport': 'sc', 'model': model,
            'y': 1, 'p': [.2, .5, .3], 'odds': [2.4, 3.1, 2.5],
            'pick': 1, 'hit': True, 'net': '2.1'}


def test_three_way_draw_is_preserved(tmp_path):
    result = audit_module.audit(write_rows(tmp_path, [row()]))['example']
    assert result['metrics'][0]['hits'] == 1
    assert result['metrics'][0]['roi'] == 2.1


def test_rejects_cross_model_price_changes(tmp_path):
    other = row('candidate')
    other['odds'][0] = 9.9
    with pytest.raises(AssertionError):
        audit_module.audit(write_rows(tmp_path, [row(), other]))


def test_rejects_wrong_stored_payout(tmp_path):
    bad = row()
    bad['net'] = '3.1'
    with pytest.raises(AssertionError):
        audit_module.audit(write_rows(tmp_path, [bad]))


def test_rejects_missing_comparator_event(tmp_path):
    extra = row()
    extra['event_id'] = 'second-event'
    with pytest.raises(AssertionError):
        audit_module.audit(write_rows(tmp_path, [row(), extra, row('candidate')]))
