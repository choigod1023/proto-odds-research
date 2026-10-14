import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('roi_policy', Path(__file__).resolve().parents[1] / 'scripts/revalidate_recommendation_policy.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TRAIN = m.timestamp('2026-09-25T13:27:49Z')
NOW = m.timestamp('2026-09-27T01:45:00Z')


def row(key='a', **kw):
    return dict(id=key, recommended=True, probability=.61, odds=1.51, sport='sc', market='test',
                kickoff_at='2026-09-26T12:00:00Z', published_at='2026-09-26T11:00:00Z',
                recorded_at='2026-09-26T11:00:00Z', result='hit', result_source='official', **kw)


def payload(*rows):
    return {'recommendation_history': {str(i):r for i,r in enumerate(rows)}}


@pytest.mark.parametrize('field,value', [
    ('recorded_at','2026-09-26T11:30:00Z'), ('published_at','2026-09-26T11:30:00Z'),
    ('recorded_at','2026-09-26T11:00:00'), ('probability', float('nan')),
    ('odds',float('inf')), ('probability',0), ('odds',1), ('recommended',False)])
def test_reject_invalid_or_late(field, value):
    r = row(); r[field] = value
    assert not m.eligible(r,NOW)


def test_future_result_is_pending_and_input_unchanged():
    r = row(); r['kickoff_at'] = '2026-09-28T00:00:00Z'
    assert m.rows_from(payload(r), {}, NOW)[0]['result'] == 'pending'
    assert r['result'] == 'hit'


def test_excludes_previous_ids_and_pre_boundary_publication():
    old = row('old'); old['recommended'] = False
    early = row('early'); early['published_at'] = TRAIN.isoformat()
    result = m.compare(payload(old), {}, payload(row('old'),early,row('new')), {}, TRAIN,NOW)
    assert result['later_unseen_events']['all']['settled'] == 1


def test_training_does_not_learn_new_outcomes():
    first = m.compare(payload(), {}, payload(row()), {},TRAIN,NOW)
    r = row(); r['result'] = 'miss'
    second = m.compare(payload(), {},payload(r),{},TRAIN,NOW)
    assert first['trained_bins'] == second['trained_bins'] == []
    assert first['later_unseen_events']['partial_pooling_value_v1']['selected'] == 0


def test_void_and_pending_are_not_losses_or_zero_roi():
    r = row(); r['result'] = 'void'
    pending = row('pending'); pending['result'] = 'pending'
    s = m.summary([r,pending])
    assert s['roi'] is None and s['pending'] == 1 and s['void'] == 1
    assert m.summary([row(),r])['roi_including_void_stakes'] == pytest.approx(.255)


def test_wilson_and_pooling():
    assert m.wilson_lower(33,41) == pytest.approx(.659864,abs=1e-5)
    key = m.bucket(row())
    model = {key: {'n':30,'hits':24}, ('bs','other',12):{'n':40,'hits':20}}
    assert m.calibrated_probability(row(),model) == pytest.approx(34/50)
    assert not m.challenger(row(),model)
    assert not m.pooled_value(row(),model)  # .68 * 1.51 - 1 < .03


def test_duplicate_event_and_bad_boundary_fail():
    with pytest.raises(ValueError,match='duplicate'):
        m.rows_from(payload(row(),row()),{},NOW)
    with pytest.raises(ValueError,match='precede'):
        m.compare(payload(),{},payload(),{},NOW,TRAIN)


def test_fixed_p60_return():
    rows = [row(str(i)) for i in range(4)]
    rows[-1]['result'] = 'miss'
    s = m.evaluate([],rows)['p60_o150']
    assert s['hits'] == 3 and s['roi'] == pytest.approx(.1325)


def test_future_snapshot_cannot_enter_training():
    with pytest.raises(ValueError,match='boundary'):
        m.rows_from(payload(row()), {'generated_at': '2026-09-28T00:00:00Z'}, NOW)


def test_future_saved_settlement_not_used_without_boundary_feed():
    r = row(); r['settled_at'] = '2026-09-28T00:00:00Z'
    assert m.rows_from(payload(r), {}, NOW)[0]['result'] == 'pending'
