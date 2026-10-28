from copy import deepcopy
from datetime import timedelta
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from ai_decision import can_apply_decision_probability,can_apply_decision_selection,validate_decision_snapshot
from tests.test_fixed_tipster_hybrid import decide,game,vote,AT
from tests.test_recommendation_history import candidate,payload,NOW
from fixed_tipster_hybrid import VERSION,context
from recommendation_history import capture_history
from scripts.run_forward_paper import tipster_rows,advance,ACCOUNT


def test_minority_market_probability_does_not_override_author_plurality():
    g=game();g['options'][0]['시장확률']=.9;g['options'][1]['시장확률']=.1
    g,s=decide(g)
    assert g['추천']['선택']=='원정'
    assert s['probability']['final']==.1 and s['probability']['market']==.1
    assert s['probability']['ai_delta_applied']==0
    assert can_apply_decision_selection(s['model'])
    assert not can_apply_decision_probability(s['model'])
    invalid=deepcopy(s);invalid['probability']['final']=1
    import pytest
    with pytest.raises(ValueError,match='unvalidated'):
        validate_decision_snapshot(invalid)


def test_ties_and_same_first_author_conflicts_withhold():
    _,s=decide(rows=[vote(),vote(slug='other',pick_id='h',selection='NYY ML -110',american_odds=-110)])
    assert s['action']=='withhold' and s['tipster_hybrid']['reason']=='tied_tipster_votes'
    g,s=decide(rows=[vote(),vote(pick_id='conflict',selection='NYY ML -110',american_odds=-110)])
    assert g['추천'] is None and s['tipster_hybrid']['authors']==0


def test_old_versions_still_authorize_their_original_frozen_probability():
    model=dict(status='operational',operating_version='market90-tipster10-v2',policy_authorized=True,
               promotion_gate='user_authorized_unvalidated',validated_edge=False)
    assert can_apply_decision_probability(model)


def paper_row(revision='first', **changes):
    return candidate(selection_policy=VERSION,selection_policy_applied=True,selection_revision=revision,
        decision_id=revision,tipster_selection=dict(version=VERSION,status='applied',selected_side='홈',
            authors=1,vote_shares={'홈':1.,'무':0.,'원정':0.},evidence_hash='evidence',
            latest_observed_at=(NOW-timedelta(minutes=1)).isoformat()),**changes)


def test_revision_history_and_later_withhold_block_stale_admission():
    first=paper_row()
    history=capture_history(payload([first]),{},NOW)
    original=deepcopy(history)
    assert len(tipster_rows(list(history.values()),NOW))==1
    later=NOW+timedelta(minutes=1)
    withdrawn=paper_row('withdraw',sel=None,odds=None,predicted_hit_prob=None)
    withdrawn['tipster_selection']=dict(first['tipster_selection'],status='withheld',selected_side=None)
    doc=payload([],later);doc['selection_withholds']=[withdrawn]
    history=capture_history(doc,{'recommendation_history':history},later)
    assert len(history)==2
    assert all(history[k]==r for k,r in original.items())
    assert tipster_rows(list(history.values()),later)==[]


def test_old_exact_revision_still_settles_after_new_pregame_pick():
    from recommendation_history import settle_history
    first=paper_row()
    h=capture_history(payload([first]),{},NOW)
    journal,_=advance([],dict(payload([]),recommendation_history=h),NOW)
    assert any(e['type']=='bet' and e['account']==ACCOUNT for e in journal)
    later=NOW+timedelta(minutes=1)
    second=paper_row('second',sel='원정')
    second['tipster_selection']=dict(first['tipster_selection'],selected_side='원정',vote_shares={'홈':0.,'무':0.,'원정':1.})
    h=capture_history(payload([second],later),{'recommendation_history':h},later)
    result={**first,'label':'','result':'홈승','n_way':2}
    done_at=NOW+timedelta(hours=5)
    settle_history(h,{'markets':{'1':{'1':result}}},done_at)
    done,_=advance(journal,dict(payload([],done_at),recommendation_history=h),done_at)
    settled=[e for e in done if e['type']=='settlement' and e['account']==ACCOUNT]
    assert len(settled)==1 and settled[0]['net']==6000
