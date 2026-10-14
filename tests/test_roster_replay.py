from copy import deepcopy
from datetime import timedelta

import pytest
from test_recommendation_history import candidate,NOW
from test_per_event_shadow import payload
from per_event_shadow import capture
from roster_replay import replay,compare_tickets


def records():
    a=candidate(predicted_hit_prob=.6)
    b=candidate(sel='원정',odds=1.9,predicted_hit_prob=.6)
    c=candidate(2,home='H2',predicted_hit_prob=.6)
    d=candidate(2,home='H2',sel='원정',odds=1.9,predicted_hit_prob=.6)
    state=capture(payload([a,b,c,d],[a,c]),{},{},NOW)
    for record in state['records'].values():
        for pick in record['candidate_roster']:
            pick.update(result='hit' if pick['sel']=='원정' else 'miss',result_source='official')
    return {'per_event_shadow':state}


def test_same_event_and_disjoint_simultaneous_pair():
    data=records(); before=deepcopy(data)
    result=replay(data,NOW+timedelta(days=1))
    assert result['eligible_same_events']==2 and result['different_picks']==2
    assert result['singles']['challenger']['roi']==pytest.approx(.9)
    assert result['pairs']['challenger']['roi']==pytest.approx(2.61)
    assert result['pairs']['common_roi_difference']==pytest.approx(3.61)
    assert data==before


def test_no_cross_batch_pair_and_future_result():
    data=records()
    next(iter(data['per_event_shadow']['records'].values()))['observation_batch_id']='different'
    result=replay(data,NOW)
    assert result['pairs']['baseline']['selected']==0
    assert result['singles']['baseline']['pending']==2


def test_missing_rosters_no_fabrication():
    data=records()
    for r in data['per_event_shadow']['records'].values():
        r.pop('candidate_roster')
    assert replay(data,NOW)['eligible_same_events']==0


def test_mixed_pending_and_void_use_common_settlement():
    hit={'odds':1.5,'result':'hit','result_source':'official'}
    void={'odds':1.8,'result':'void','result_source':'official'}
    pending={'odds':1.5,'result':'pending'}
    result=compare_tickets([[hit,void],[hit]],[[hit,void],[pending]])
    assert result['baseline']['settled']==2
    assert result['common_settled']==1
    assert result['common_roi_difference']==0


def test_invalid_freshness_and_wrong_event_rejected():
    data=records(); entries=list(data['per_event_shadow']['records'].values())
    entries[0]['live_odds_at']=(NOW-timedelta(hours=1)).isoformat()
    entries[1]['candidate_roster'][0]['home']='wrong'
    assert replay(data,NOW+timedelta(days=1))['eligible_same_events']==0
