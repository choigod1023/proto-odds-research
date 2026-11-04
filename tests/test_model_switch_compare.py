from copy import deepcopy
from decimal import Decimal
import json
import numpy as np
import pytest

from research.model_switch import compare as c
from research.model_switch.adoption import recommendation


def sample():
    r={'fixture_id':'f1','official_id':'g1','market_id':'proto:1:1:full_game_2way','kickoff':'2026-04-01T18:30:00+09:00',
       'prices':{'home':1.5,'away':2.4},'scores':[3,2],'winner':'home','strict_failure_reasons':[],
       'price_time_verified':False,'votes':[{'source':'site','author':'a','side':'away','observed_at':'2026-05-01T10:00:00Z','url':'https://example.test/pick','sha256':'a'*64}],
       'model_home_probabilities':{c.M0:.6},'selections':{'market':'home','vote':'away',c.M0:'home'},
       'profit':{'market':.5,'vote':-1,c.M0:.5}}
    m={'f1':{'event_date':'2026-04-01','probabilities_2way':{'home':.6,'away':.4}}}
    return r,m


def test_exact_shared_prices_and_recomputed_settlement():
    r,m=sample();rows,failures=c.normalize([r],m)
    assert rows[0]['profit']=={'vote':'-1','market':'0.5','m0_actual':'0.5'}
    assert rows[0]['strict_pregame_eligible'] is False
    assert failures['original_vote_observed_after_t30']==1
    r['price_time_verified']=True
    assert c.normalize([r],m)[0][0]['strict_pregame_eligible'] is False


@pytest.mark.parametrize('defect',['odds','duplicate','winner','market','model','selection','profit'])
def test_mismatched_binding_fails(defect):
    r,m=sample();rs=[r]
    if defect=='odds':r['prices']['away']=float('nan')
    elif defect=='duplicate':rs.append(deepcopy(r))
    elif defect=='winner':r['winner']='away'
    elif defect=='market':r['market_id']='other'
    elif defect=='model':m['f1']['probabilities_2way']['home']=.3
    elif defect=='selection':r['selections']['market']='away'
    else:r['profit']['market']=-1
    with pytest.raises(ValueError):c.normalize(rs,m)


def test_author_first_vote_and_simultaneous_conflict():
    r,_=sample();v=r['votes'][0]
    assert c.votes([v,v])==('away',1)
    assert c.votes([v,dict(v,side='home')])==(None,0)
    assert c.votes([v,dict(v,side='home',observed_at='2026-06-01T00:00:00Z')])==('away',1)


def test_draw_refund_and_daily_drawdown_are_separate_from_accuracy():
    r,m=sample();r.update(scores=[3,3],winner='draw',profit={'vote':0,'market':0,c.M0:0})
    rows,_=c.normalize([r],m);dates,x=c.daily_arrays(rows);stats=c.metrics(rows,'vote',dates,x)
    assert stats['refunds']==1 and stats['accuracy'] is None and stats['roi']==0
    assert c.drawdown([2,-3,2,-5])==6


def test_bootstrap_preserves_pairing_and_is_reproducible():
    # All arms identical => every paired interval exactly zero.
    values=np.array([[1,1,1,1,1,1,1,1],[1,1,-1,-1,-1,0,0,0],[0,0,0,0,0,0,0,0]],float)
    a=c.bootstrap(values,2,500,1)
    assert a==c.bootstrap(values,2,500,1)
    for v in a.values():assert v['roi_delta_adjusted_interval']==[0,0] and v['accuracy_delta_adjusted_interval']==[0,0]


def test_simultaneous_exposure_prevents_sixth_bet_and_never_spends_today_winnings():
    rows=[{'date':'2026-04-01','profit':{'vote':'1'}} for _ in range(6)]
    result=c.capped_wallet(rows,'vote')
    assert result['bets']==5 and result['skipped']==1 and result['net_krw']=='50000'
    losses=[{'date':'2026-04-01','profit':{'vote':'-1'}} for _ in range(6)]
    result=c.capped_wallet(losses,'vote');assert result['net_krw']=='-50000'
    assert c.capped_wallet(losses+[{'date':'2026-05-01','profit':{'vote':'-1'}}],'vote')['net_krw']=='-60000'


def evidence():
    return dict(candidate='m0_actual',source_audit_status='verified_pregame_common',common_settled=500,distinct_observed_weeks=10,candidate_frozen_before_evaluation=True,
                independent_prospective_evaluation=True,evaluation_windows_predeclared=True,
                windows=[{'start':'2027-04-01','end':'2027-05-31','roi_delta_vs_vote':.02,'roi_delta_vs_market':.02,
                          'common_settled_by_date':{d:50 for d in ('2027-04-01','2027-04-08','2027-04-15','2027-04-22','2027-04-29')}},
                         {'start':'2027-06-01','end':'2027-07-31','roi_delta_vs_vote':.01,'roi_delta_vs_market':.01,
                          'common_settled_by_date':{d:50 for d in ('2027-06-01','2027-06-08','2027-06-15','2027-06-22','2027-06-29')}}],
                adjusted_roi_delta_lower_vs_vote=.01,adjusted_roi_delta_lower_vs_market=.005,
                adjusted_accuracy_delta_lower_vs_vote=0,drawdown_delta_vs_vote=-1,
                lower_tail_loss_delta_vs_vote=-1,loss_capped_paper_loss_delta_vs_vote=-1000,
                real_settlement_clocks_verified=True,runtime_and_source_tests_passed=True)


def test_relative_loss_reduction_can_pass_review_without_auto_switch_or_profit_claim():
    out=recommendation(evidence())
    assert out['status']=='eligible_for_human_release_review'
    assert out['auto_switch'] is False and out['absolute_profit_proven'] is False


@pytest.mark.parametrize('defect',['source','reused','zero','small','weeks','overlap','uncertain','risk','clock','nan'])
def test_adoption_fails_closed(defect):
    e=evidence()
    if defect=='source':e['source_audit_status']='publication_claim'
    elif defect=='reused':e['independent_prospective_evaluation']=False
    elif defect=='zero':e['common_settled']=0
    elif defect=='small':e['common_settled']=199
    elif defect=='weeks':e['distinct_observed_weeks']=7
    elif defect=='overlap':e['windows'][1]['start']='2027-05-30'
    elif defect=='uncertain':e['adjusted_roi_delta_lower_vs_vote']=-.01
    elif defect=='risk':e['drawdown_delta_vs_vote']=1
    elif defect=='clock':e['real_settlement_clocks_verified']=False
    else:e['adjusted_accuracy_delta_lower_vs_vote']=float('nan')
    out=recommendation(e);assert out['status']=='hold' and not out['auto_switch']


@pytest.mark.parametrize('defect',['short_windows','date_outside_window','wrong_total','invented_weeks'])
def test_observed_weeks_and_counts_must_belong_to_evaluation_windows(defect):
    e=evidence()
    if defect=='short_windows':
        for w in e['windows']:w['end']=w['start']
    elif defect=='date_outside_window':e['windows'][0]['common_settled_by_date']={'2028-01-01':250}
    elif defect=='wrong_total':e['windows'][0]['common_settled_by_date']['2027-04-01']=49
    else:e['distinct_observed_weeks']=12
    out=recommendation(e)
    assert out['status']=='hold' and 'window_observation_counts_or_weeks_inconsistent' in out['failure_reasons']


def test_market_candidate_compares_against_vote_without_requiring_it_to_beat_itself():
    e=evidence();e['candidate']='market';e.pop('adjusted_roi_delta_lower_vs_market')
    for window in e['windows']:window.pop('roi_delta_vs_market')
    assert recommendation(e)['status']=='eligible_for_human_release_review'


def test_unsupported_candidate_cannot_be_promoted():
    e=evidence();e['candidate']='unfrozen_other_model'
    assert recommendation(e)['status']=='hold'
