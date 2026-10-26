import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from replay_multisport_tipsters91 import choices, score, capped_proxy
import pytest


def row(**kw):
    r = dict(sport='bk', probability=[.51,.49], odds=[1.8,2.0], y=1,
             choices={'tipster_only':1}, date='2025-01-01', home='a', away='b') | kw
    r['decisions'] = choices(r)
    return r


def test_fixed_weight_and_result_blind_selection():
    r = row()
    assert r['decisions']['research_baseline'] == 0
    assert r['decisions']['research90_tipster10'] == 1
    assert choices(row(y=0)) == r['decisions']
    assert score([r], 'research90_tipster10')['roi_pct'] == 100
    assert score([r], 'research_baseline')['roi_pct'] == -100


def test_draw_retained_and_invalid_rejected():
    r = row(sport='sc', probability=[.3,.4,.3], odds=[3,3,3], choices={'tipster_only':1})
    assert r['decisions']['research90_tipster10'] == 1
    with pytest.raises(ValueError): row(probability=[.4,.4])
    with pytest.raises(ValueError): row(odds=[float('nan'),2])


def test_cap_reserves_pending_exposure_before_settling():
    rows = [row(home=str(i),y=0) for i in range(7)]
    result = capped_proxy(rows, 'tipster')
    assert result['n'] == 5
    assert result['monthly_net']['2025-01'] == -50000
    assert score([], 'tipster')['roi_pct'] is None
