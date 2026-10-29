from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from retune_pair_audit import audit


def test_higher_hit_rate_can_still_have_lower_profit_due_to_odds():
    rows=[dict(date='2026-04-01',hg=1,ag=0,prices=[5.,1.1],baseline_probability=.6,candidate_probability=.4),
          dict(date='2026-04-02',hg=1,ag=0,prices=[1.1,5.],baseline_probability=.4,candidate_probability=.6),
          dict(date='2026-04-03',hg=1,ag=0,prices=[1.1,5.],baseline_probability=.4,candidate_probability=.6)]
    a=audit(rows)
    assert a['candidate']['wins']==2 and a['baseline']['wins']==1
    assert a['decomposition']['gained_wins']==2 and a['decomposition']['lost_wins']==1
    assert a['decomposition']['net_improvement_krw']==-28000


def test_single_odds_are_not_silently_rounded_up_and_draw_is_refunded():
    row=dict(date='2026-04-01',hg=2,ag=1,prices=[1.49,2.2],order=['home','away'],baseline_probability=.6,candidate_probability=.7)
    result=audit([row,dict(row,hg=1,ag=1)])
    assert result['candidate']['net_krw']==4900 and result['candidate']['roi']==.245
    assert result['candidate']['accuracy']==1 and result['candidate']['pushes']==1


def test_reversed_price_order_is_respected_and_same_picks_cannot_change_profit():
    row=dict(date='2026-04-01',hg=0,ag=2,prices=[2.11,1.51],order=['away','home'],baseline_probability=.4,candidate_probability=.3)
    result=audit([row]);assert result['candidate']['net_krw']==11100
    assert result['decomposition']['net_improvement_krw']==0 and result['pick_changes']==0


def test_invalid_probabilities_are_not_counted_as_away_picks():
    with pytest.raises(ValueError,match='probability'):
        audit([dict(date='2026-04-01',hg=0,ag=1,prices=[1.5,2.1],baseline_probability=.5,candidate_probability=float('nan'))])
