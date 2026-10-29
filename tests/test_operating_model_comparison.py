from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from operating_ledger_inputs import normalize_operating_prediction
from operating_model_comparison import compare, replay_arms
from test_operating_ledger_inputs import operating_record as _new_record

AT = '2026-10-27T12:00:00Z'


def operating_record(**kwargs):
    row,game=_new_record(**kwargs)
    row['features'].pop('model_comparison',None)  # explicit historical fixture
    return row,game


def official(row, *, score=(2,1), at=AT, state='confirmed', game_no='10'):
    f=row['features']['fixture']
    kickoff=datetime.fromisoformat(row['kickoff'].replace('Z','+00:00'))
    return dict(source='proto',season=f['year'],round=f['round'],game_no=game_no,
                kickoff=(kickoff+timedelta(hours=9)).replace(tzinfo=None).isoformat(),
                sport=f['sport'],league=f['league'],home_team=f['home'],away_team=f['away'],
                home_score=score[0],away_score=score[1],observed_at=at,result_state=state)


def run(rows,results):
    return compare(rows,results,evaluated_at='2026-10-29T13:00:00Z')


def test_real_snapshot_replay_and_same_electorate_counterfactual_are_distinct():
    row,_=operating_record()
    arms,reason=replay_arms(normalize_operating_prediction(row))
    assert reason is None
    assert arms['operating']['selection']=='원정'
    assert arms['operating']['probability']==.48
    assert arms['fixed91_same_electorate']['selection']=='원정'
    assert arms['market_reference']['selection']=='홈'
    assert arms['home_always']['probability'] is None
    assert arms['saved_score_reference']['status']=='unavailable'


def test_known_integer_money_hit_loss_and_same_fixture_labels():
    row,_=operating_record()
    out=run([row],[official(row)])
    p=out['policies']['tipster-vote-v1']['all_opportunities']
    assert p['operating']['net_won']==-10000
    assert p['operating']['roi']==-1
    assert p['market_reference']['net_won']==4900
    assert p['market_reference']['roi']==.49
    assert out['events'][0]['official']['winner']=='홈'
    assert not out['hash_chain_verified']


def test_withheld_latest_revision_is_abstention_not_older_pick_fallback():
    old,_=operating_record()
    new=deepcopy(old)
    new['snapshot_id']=new['decision_id']='new'
    new['ledger_sequence']=2
    new['captured_at']='2026-10-25T22:01:00Z'
    new['predictions']={'action':'withhold'}
    new['model'].update(operating_version='shin-market-anchor-v1',affects_selection=False,
                        policy_authorized=False,status='unavailable')
    new['versions']['operating_model']='shin-market-anchor-v1'
    new['features']['tipster_hybrid'].update(status='withheld',vote_counts={},vote_shares={})
    out=run([old,new],[official(old)])
    assert len(out['events'])==1
    assert out['events'][0]['snapshot_id']=='new'
    p=out['policies']['tipster-vote-v1']['all_opportunities']
    assert p['operating']['abstained']==1 and p['operating']['selections']==0
    assert p['operating']['roi'] is None  # no turnover is not 0% ROI
    assert p['market_reference']['selections']==1
    assert p['fixed91_same_electorate']['unavailable']==1


def test_future_active_feature_never_relaxed_for_abstentions():
    row,_=operating_record()
    row['features']['player_context']['updated_at']='2026-10-25T22:10:00Z'
    out=run([row],[official(row)])
    assert not out['events']
    assert 'feature_after_as_of' in out['excluded'][0]['reason']


@pytest.mark.parametrize('correction', ['unconfirmed','tie_conflict','changed_score'])
def test_corrections_resolved_before_confirmed_filter(correction):
    row,_=operating_record()
    first=official(row,at='2026-10-27T11:00:00Z')
    latest=official(row)
    if correction=='unconfirmed':
        latest.update(result_state='unconfirmed',kickoff='0001-01-01T00:00:00',home_team='',away_team='')
    else:
        latest.update(home_score=0,away_score=2)
    labels=[first,latest]
    if correction=='tie_conflict':labels.append(official(row))
    out=run([row],labels)
    event=out['events'][0]
    if correction=='changed_score':
        assert event['official']['winner']=='원정'
        assert out['policies']['tipster-vote-v1']['all_opportunities']['operating']['net_won']==11000
    else:assert event['label_status']=='missing_official_result'
    assert run([row],list(reversed(labels)))==out


def test_label_mutation_cannot_change_choices_and_source_unchanged():
    row,_=operating_record()
    original=deepcopy(row)
    a=run([row],[official(row,score=(2,1))])
    b=run([row],[official(row,score=(0,3))])
    assert a['events'][0]['arms']==b['events'][0]['arms']
    assert a['events'][0]['input_hash']==b['events'][0]['input_hash']
    assert a['events'][0]['official']!=b['events'][0]['official']
    assert row==original


def test_duplicate_physical_event_ids_never_inflate_sample():
    row,_=operating_record()
    duplicate=deepcopy(row)
    duplicate['event_id']='reissued'
    out=run([row,duplicate],[official(row)])
    assert not out['events']
    assert out['exclusion_counts']=={'duplicate_physical_fixture':2}


def test_ambiguous_full_game_offers_fail_closed():
    row,_=operating_record()
    row['features']['options'].append(deepcopy(row['features']['options'][0]))
    out=run([row],[official(row)])
    assert not out['events']


@pytest.mark.parametrize('change', ['wrong_round','wrong_teams','future_observation','before_kickoff','aware_source_kickoff'])
def test_do_not_join_uncertain_or_time_ineligible_official_labels(change):
    row,_=operating_record()
    label=official(row)
    if change=='wrong_round':label['round']+=1
    if change=='wrong_teams':label['home_team']='different'
    if change=='future_observation':label['observed_at']='2026-10-30T12:00:00Z'
    if change=='before_kickoff':label['observed_at']=row['as_of']
    if change=='aware_source_kickoff':label['kickoff']+='Z'
    assert run([row],[label])['events'][0]['label_status']=='missing_official_result'


def test_two_way_draw_is_refund_not_loss_and_void_stake_is_disclosed():
    row,_=operating_record()
    out=run([row],[official(row,score=(1,1))])
    s=out['policies']['tipster-vote-v1']['all_opportunities']['operating']
    assert s['voids']==1 and s['net_won']==0
    assert s['settled_turnover_won']==10000 and s['roi']==0
    assert s['hit_rate'] is None


def test_missing_outcomes_stay_pending_without_counting_losses():
    row,_=operating_record()
    s=run([row],[])['policies']['tipster-vote-v1']['all_opportunities']['operating']
    assert s['pending']==1 and s['settled']==0 and s['net_won']==0 and s['roi'] is None


def test_score_control_requires_complete_saved_probability_vector():
    row,_=operating_record()
    for i,o in enumerate(row['features']['options'][:2]):o['score_model_probability']=[.2,.8][i]
    arms,_=replay_arms(normalize_operating_prediction(row))
    assert arms['saved_score_reference']['selection']=='원정'
    row['features']['options'][0]['score_model_probability']=None
    arms,_=replay_arms(normalize_operating_prediction(row))
    assert arms['saved_score_reference']['status']=='unavailable'


def test_future_saved_controls_are_used_and_never_silently_refitted():
    row,_=_new_record()
    arms,reason=replay_arms(normalize_operating_prediction(row))
    assert reason is None and arms['operating']['selection']=='원정'
    row['features']['model_comparison']['arms']['market_reference']['selection']='원정'
    arms,reason=replay_arms(normalize_operating_prediction(row))
    assert arms is None and reason=='saved_prospective_control_contract_mismatch'


def test_changed_future_recipe_implementation_is_an_explicit_blocker():
    row,_=_new_record()
    row['features']['model_comparison']['implementation_hash']='other-code-version'
    assert replay_arms(normalize_operating_prediction(row))[1]=='saved_prospective_control_contract_mismatch'


def test_saved_pick_price_roi_does_not_require_inventing_missing_probability():
    row,_=operating_record()
    row['predictions']['probability']=None
    row['predictions']['probability_detail']={}
    out=run([row],[official(row)])
    assert out['events'][0]['arms']['operating']['probability'] is None
    assert out['policies']['tipster-vote-v1']['all_opportunities']['operating']['net_won']==-10000


def test_absent_fixture_is_not_falsely_reported_as_duplicate_game():
    a,_=operating_record()
    b=deepcopy(a)
    a['features'].pop('fixture')
    b['features'].pop('fixture')
    b['event_id']='another'
    out=run([a,b],[])
    assert out['exclusion_counts']=={'missing_physical_fixture_identity':2}
