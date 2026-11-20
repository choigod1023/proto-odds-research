from pathlib import Path
import copy,sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from retune_selection_stability import prepare,choose,analyze


def rows():
    return [dict(date='2024-04-01',hg=2,ag=1,prices=[1.8,2.0],market_probability=.6,candidate_probabilities={'signal':.7}),
            dict(date='2024-04-08',hg=1,ag=2,prices=[1.8,2.0],market_probability=.4,candidate_probabilities={'signal':.3})]


def test_winner_stability_preserves_fixed_better_calibrated_candidate():
    result=analyze(rows(),100)
    assert result['original_primary']=='signal' and result['original_primary_share']==1.
    assert result['observed_validation_weeks']==2 and result['candidate_count']==2
    assert result['weighted_mean_probability_shift_from_original']==0 and result['weighted_validation_pick_disagreement_from_original']==0


def test_more_hits_but_worse_roi_is_not_primary_eligible():
    r=[dict(date='2024-04-01',hg=2,ag=1,prices=[5.,1.1],market_probability=.6,candidate_probabilities={'hits':.4}),
       dict(date='2024-04-08',hg=2,ag=1,prices=[1.1,5.],market_probability=.4,candidate_probabilities={'hits':.6}),
       dict(date='2024-04-15',hg=2,ag=1,prices=[1.1,5.],market_probability=.4,candidate_probabilities={'hits':.6})]
    names,_,tensor=prepare(r);assert choose(names,tensor.sum(axis=0))=='market_null'


def test_missing_candidate_predictions_cannot_silently_select_easy_subset():
    r=rows();r[1]['candidate_probabilities']={'another':.3}
    with pytest.raises(ValueError,match='coverage'):prepare(r)


def test_refund_does_not_enter_accuracy_or_brier_denominator():
    r=rows();r.append(dict(r[0],hg=1,ag=1))
    _,_,tensor=prepare(r);sums=tensor.sum(axis=0)
    assert sums[0,0]==3 and sums[0,1]==2 and sums[0,2]==2


def test_stability_uses_validation_only_and_rejects_nonfinite_probability():
    r=rows();before=analyze(r,50);r[0]['evaluation_roi']=999;assert analyze(r,50)==before
    r[0]['candidate_probabilities']['signal']=float('nan')
    with pytest.raises(ValueError,match='probability'):prepare(r)
