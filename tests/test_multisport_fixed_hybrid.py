import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('multi', Path(__file__).parents[1] / 'scripts/compare_multisport_fixed_hybrid.py')
multi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(multi)


def row(sport='bk'):
    return dict(sport=sport, date='2025-01-01', home='A', away='B',
                probability=[.6, .4], odds=[1.6, 2.2], y=1, choices={'tipster_only': 1})


def test_exact_fixed_weight_flips_binary():
    assert multi.decisions(row()) == dict(baseline=0, tipster=1, hybrid75_25=1)
    assert multi.metrics([row()], 'hybrid75_25')['roi_pct'] == pytest.approx(120)


def test_soccer_keeps_draw():
    r = dict(row('sc'), probability=[.3, .4, .3], odds=[3., 3.2, 2.8], y=1, choices={'tipster_only': 1})
    assert multi.decisions(r)['hybrid75_25'] == 1


def test_outcome_does_not_change_selection():
    a = row()
    assert multi.decisions(a) == multi.decisions(dict(a, y=0))


def test_empty_is_not_zero_roi():
    assert multi.evaluate([])['vl']['scores']['hybrid75_25']['roi_pct'] is None


def test_duplicate_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        multi.evaluate([row(), row()])


@pytest.mark.parametrize('updates', [dict(probability=[.7,.4]), dict(probability=[float('nan'),.4]),
    dict(odds=[0,2.]), dict(sport='sc'), dict(y=3), dict(choices={'tipster_only':-1}),
    dict(tipster_distribution=[1.,0.,0.])])
def test_bad_inputs(updates):
    with pytest.raises(ValueError):
        multi.decisions(dict(row(), **updates))
