import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('cal',Path(__file__).parents[1]/'scripts/calibrate_context_hybrid.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(i,day=1,y=0):
    return dict(id=str(i),date=f'2025-01-{day:02d}',sport='bs',league='MLB',
                at=f'2025-01-{day:02d}T12:00:00+00:00',snapshot_at=f'2025-01-{day:02d}T11:00:00+00:00',
                kickoff=f'2025-01-{day:02d}T14:00:00+00:00',settled_at=f'2025-01-{day:02d}T18:00:00+00:00',
                model=[.6,.4],tipster=[1.,0.],odds=[1.6,2.1],y=y)


def test_insufficient_history_preserves_fixed_blend():
    r=m.forward([row(1)])[0]
    assert not r['calibration_active'] and r['weight']==.25
    assert r['forecasts']['context_hybrid']==pytest.approx(.7)


def test_current_and_future_labels_do_not_change_predictions():
    rows=[row(i,1+i//12,i%2) for i in range(36)]
    a=m.forward(rows)
    changed=[dict(r,y=1-r['y']) if r['date']>='2025-01-03' else r for r in rows]
    b=m.forward(changed)
    assert [r['forecasts'] for r in a]==[r['forecasts'] for r in b]
    assert a[-1]['calibration_active']


def test_delayed_results_not_available():
    rows=[dict(row(i,1+i//12,i%2),settled_at='2025-02-01T00:00:00+00:00') for i in range(36)]
    assert all(not r['calibration_active'] for r in m.forward(rows))


def test_extreme_vote_calibration_finite_and_shrunk():
    past=[row(i,1+i//12,i%2) for i in range(24)]
    ab,active=m.fit_calibrator(past,'tipster')
    assert active
    assert 0<m.calibrate(1.,ab,active)<1
    assert 0<m.calibrate(0.,ab,active)<1


def test_context_fallback_and_actual_switch_use_old_predictions():
    target=row(100,5)
    history=[]
    for i in range(24):
        r=row(i,1+i//8,0)
        history.append(dict(r,context=m.context(r),candidates={str(w):1-w/2 for w in m.WEIGHTS}))
    assert m.select_weight(history,target)[0]==0.
    assert m.select_weight(history[:10],target)[0]==.25
    assert m.select_weight(history,dict(target,league='KBO'))[0]==.25


def test_duplicate_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        m.forward([row(1),row(1)])


def test_pregame_required():
    with pytest.raises(ValueError,match='pregame'):
        m.forward([dict(row(1),snapshot_at='2025-01-02T00:00:00+00:00')])


def test_three_way_not_silently_treated_as_binary():
    with pytest.raises(ValueError,match='Binary'):
        m.forward([dict(row(1),model=[.4,.3,.3])])


def test_input_order_invariant():
    rows=[row(i,1+i//12,i%2) for i in range(36)]
    assert m.forward(rows)==m.forward(list(reversed(rows)))
