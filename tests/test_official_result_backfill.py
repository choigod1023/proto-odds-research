from copy import deepcopy

import pytest

from src.official_result_backfill import plan_backfill, apply_backfill
from tests.test_live_ledger_settlement import setup_runtime
from tests.test_live_market_refresh import admissible_votes_for_offer_ledger_regressions

SOURCE = {'name': 'test_fixture_export', 'provenance': 'synthetic'}
TIME = '2027-01-01T00:00:00+00:00'


def inputs(tmp_path):
    runtime, _, feed = setup_runtime(tmp_path)
    row = {**feed['markets']['102']['7100'], 'year': 2026, 'round': 102}
    sid = runtime.records()[0]['snapshot_id']
    return runtime, row, sid


def plan(runtime, row, sid):
    return plan_backfill(runtime.ledger.records(), [row], snapshot_ids=[sid], observed_at=TIME, source=SOURCE)


def test_old_round_dry_run_apply_and_retry(tmp_path):
    runtime, row, sid = inputs(tmp_path)
    before = deepcopy(runtime.ledger.records())
    p = plan(runtime, row, sid)
    assert len(p['entries']) == 1 and not p['holds']
    assert runtime.ledger.records() == before
    # Use a settled timestamp accepted by the synthetic ledger clock.
    runtime.ledger._clock = lambda: __import__('datetime').datetime(2027, 1, 2, tzinfo=__import__('datetime').timezone.utc)
    assert apply_backfill(runtime.ledger, p, [row], observed_at=TIME, source=SOURCE)[0].appended
    after = runtime.ledger.records()
    assert after[0] == before[0]
    assert after[-1]['snapshot_id'] == sid and after[-1]['outcome']['result'] == 'hit'
    assert plan(runtime, row, sid)['holds'][0]['reason'] == 'existing_settlement'
    with pytest.raises(ValueError):
        apply_backfill(runtime.ledger, p, [row], observed_at=TIME, source=SOURCE)


@pytest.mark.parametrize('field,value', [('year', 2025), ('round', 103), ('game_no', '9999'),
    ('n_way', 3), ('market', '승무패'), ('label', 'changed'), ('date', '08.29(토) 18:00'),
    ('home', 'other'), ('result', '미확인')])
def test_exact_identity_required(tmp_path, field, value):
    runtime, row, sid = inputs(tmp_path)
    row[field] = value
    assert not plan(runtime, row, sid)['entries']


def test_duplicate_rows_and_changed_plan_are_held(tmp_path):
    runtime, row, sid = inputs(tmp_path)
    p = plan(runtime, row, sid)
    dup = {**row, 'result': '홈패'}
    assert not plan_backfill(runtime.ledger.records(), [row, dup], snapshot_ids=[sid], observed_at=TIME, source=SOURCE)['entries']
    with pytest.raises(ValueError):
        apply_backfill(runtime.ledger, p, [dup], observed_at=TIME, source=SOURCE)
    assert len(runtime.ledger.records()) == 1


def test_latest_withhold_does_not_resurrect_selection(tmp_path):
    runtime, row, sid = inputs(tmp_path)
    records = runtime.ledger.records()
    newer = deepcopy(records[0])
    newer.update(snapshot_id='withhold', as_of='2026-08-30T02:00:00+00:00', ledger_sequence=2)
    newer['predictions'] = {'action': 'withhold'}
    records.append(newer)
    p = plan_backfill(records, [row], snapshot_ids=[sid, 'withhold'], observed_at=TIME, source=SOURCE)
    assert not p['entries']
    assert {h['reason'] for h in p['holds']} == {'latest_not_selected', 'not_latest_or_missing'}
