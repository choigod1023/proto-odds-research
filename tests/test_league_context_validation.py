import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from league_context_validation import validate_rows, split_rows, choose_alpha, fit_predict, metrics, evaluate, matched_policy


def row(i=0, **changes):
    at = datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
    r = dict(event_id=str(i), league='test', kickoff=at.isoformat(),
             feature_as_of=(at-timedelta(days=1)).isoformat(), features=[i%4, i%3],
             target=i%3, odds=[2., 3., 4.], odds_as_of=None)
    return dict(r, **changes)


def test_future_and_naive_time_rejected():
    r = row()
    assert not validate_rows([dict(r, feature_as_of=r['kickoff'])])[0]
    assert not validate_rows([dict(r, odds_as_of=r['kickoff'])])[0]
    assert not validate_rows([dict(r, feature_as_of='2022-12-31')])[0]
    assert validate_rows([r])[0][0]['odds_as_of'] is None


def test_conflicting_duplicate_not_selected_by_result():
    r = row()
    assert len(validate_rows([r, r])[0]) == 1
    assert not validate_rows([r, dict(r, target=2)])[0]
    assert not validate_rows([r, dict(r, target=2, features=[])])[0]
    assert not validate_rows([row(event_id=1), row(league=1), []])[0]


def test_matched_counts_do_not_rank_by_result():
    def pick(i, hit):
        return dict(selection_id=str(i), kickoff_at=row()['kickoff'], odds=1.55, hit=hit, predicted_hit_prob=.6-i*.01)
    result=matched_policy([pick(0,0),pick(1,1)],[pick(0,1)])
    assert result['market']['n']==result['selected']['n']==1
    assert result['market']['hits']==0


@pytest.mark.parametrize('changes', [{'features':[float('nan')]}, {'odds':[2.,float('inf')]}, {'target':True}, {'features':[]}])
def test_invalid_data(changes):
    assert not validate_rows([row(**changes)])[0]


def test_dates_embargo_and_independent_small_leagues():
    tr, va, te = split_rows([row(i) for i in range(500)])
    assert datetime.fromisoformat(va[0]['kickoff'])-datetime.fromisoformat(tr[-1]['kickoff']) > timedelta(days=7)
    assert datetime.fromisoformat(te[0]['kickoff'])-datetime.fromisoformat(va[-1]['kickoff']) > timedelta(days=7)
    result = evaluate([row(i, league=str(i%2)) for i in range(40)], 'unused')
    assert all(r['status']=='insufficient_data' for r in result['leagues'].values())
    assert result['production_allowed'] is False


def test_identical_candidate_chooses_no_adjustment():
    rows = [row(i) for i in range(90)]
    p = np.tile([.5,.3,.2], (90,1))
    assert choose_alpha(rows,p,p)==0


def test_draw_is_a_full_class_and_test_labels_not_used():
    train, test = [row(i) for i in range(200)], [row(i) for i in range(201,220)]
    p = fit_predict(train, test)
    assert p.shape == (19,3)
    np.testing.assert_allclose(p.sum(axis=1),1)
    changed = [dict(r,target=2) for r in test]
    np.testing.assert_allclose(p,fit_predict(train,changed))
    assert metrics(test,p)['n']==19
