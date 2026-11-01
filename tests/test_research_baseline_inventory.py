from copy import deepcopy
import hashlib
import json

import pytest

from src.research_baseline_inventory import audit, inventory
from test_operating_ledger_inputs import operating_record


def test_revisions_count_once_and_historical_policy_not_promoted():
    current, _ = operating_record()
    old, _ = operating_record(votes=False)
    old['event_id'] = 'other-fixture'
    report = inventory([current, deepcopy(current), old])
    assert report['records'] == 3 and report['selected_events'] == 2
    assert report['input_eligible_selected_events'] == 2
    assert report['current_policy_input_eligible_selected_events'] == 1
    assert not report['roi_evaluable'] and not report['external_receipt_verified']


def test_result_records_not_used_and_late_revision_cannot_add_a_match():
    row, _ = operating_record()
    late = deepcopy(row)
    late['captured_at'] = late['as_of'] = row['kickoff']
    report = inventory([row, late, dict(record_type='settlement', event_id=row['event_id'], outcome='home')])
    assert report['selected_events'] == 1
    assert report['raw_distinct_prediction_event_ids'] == 1
    assert report['rejected_revisions_or_events'] == 1
    assert report['exclusion_reasons_overlapping']['not_before_cutoff'] == 1
    assert report['result_used'] is False


def test_invalid_latest_revision_does_not_restore_old_good_prediction():
    row, _ = operating_record()
    bad = deepcopy(row)
    bad['as_of'] = bad['captured_at'] = '2026-10-25T22:01:00Z'
    bad['features']['official_result'] = 'away'
    report = inventory([row, bad])
    assert report['selected_events'] == 1
    assert report['input_eligible_selected_events'] == 0
    assert any('result_in_features' in key for key in report['exclusion_reasons_overlapping'])


def test_source_bytes_are_pinned_and_empty_is_not_roi_ready(tmp_path):
    source = tmp_path / 'ledger.jsonl'
    row, _ = operating_record()
    payload = (json.dumps(row)+'\n').encode()
    source.write_bytes(payload)
    before = deepcopy(row)
    report = audit(source)
    assert report['source_sha256'] == hashlib.sha256(payload).hexdigest()
    assert source.read_bytes() == payload
    assert inventory([row])['input_eligible_selected_events'] == 1 and row == before
    empty = inventory([])
    assert empty['selected_events'] == 0 and empty['prediction_as_of_range_utc'] is None
    assert not empty['roi_evaluable']


def test_sql_wrappers_and_nonobjects_rejected():
    with pytest.raises(ValueError):
        inventory([{'record_json': '{}'}])
    with pytest.raises(ValueError):
        inventory([None])
