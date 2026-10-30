import copy
from datetime import timedelta
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('two_leg_research',Path(__file__).parents[1]/'scripts/two_leg_literature_research.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def params():
    return {'temps':{'0.0':1.,'0.1':1.},'weight':None,'leagues':{}}


def raw(event='A',available='2026-10-30T16:00:00+09:00',kickoff='2026-10-30T19:00:00+09:00',p=.7,odds=(1.6,2.3),tipster=True):
    f=dict(cohort='retrospective_test' if tipster else 'saved_pregame',event_id=event,snapshot_id=event+available,
           league='KBO',home=event+'home',away=event+'away',kickoff=kickoff,available_at=available,
           quote_observed_at=available,availability_proven=not tipster,voters=1)
    f['raw_forecasts']=[dict(source='site',author='author',side='home',document_sha256=event,
                             source_reported_published_at='2026-10-29T10:00:00+09:00')]
    f['choices']=[dict(offer_id=event+str(i),selection=['home','away'][i],market='full',line=None,issue_year=2026,
                       side=['home','away'][i],
                       round=100,game_no=ord(event),odds=odds[i],scores={'market':[p,1-p][i],
                       'vote_support':[1.,0.][i],'saved_score_shadow':[p,1-p][i],
                       'saved_final_reference':[p,1-p][i]},probability_models=['market','saved_score_shadow'],
                       hit=i==0,refund=False)for i in range(2)]
    return f


def prepared(*frames):return m.prepare(frames)[0]


def pick(frames,model='fixed_market90_external10',rule='conditional2'):
    return m.pick_pair(frames,[2026,100],model,rule,params(),params())


def test_temperature_positive_preserves_argmax_and_identity():
    for p in ([.2,.8],[.7,.2,.1],[0.,.6,.4]):
        for t in m.TEMPERATURES:
            q=m.temperature(p,t)
            assert max(range(len(q)),key=q.__getitem__)==max(range(len(p)),key=p.__getitem__)
            assert abs(sum(q)-1)<1e-12


def test_invalid_probability_units_are_not_normalized_into_evidence():
    assert m.vector([.6,.7]) is None
    assert m.vector([-.1,1.1]) is None
    assert m.vector([None,.4]) is None


def test_no_filler_if_only_one_individually_eligible_event():
    a,b=prepared(raw('A'),raw('B',p=.5,odds=(1.01,1.01)))
    assert pick([a,b]) is None
    assert pick([a,b],rule='forced2') is not None


def test_conditional_requires_probability_and_value_without_odds_ceiling():
    a,b=prepared(raw('A',p=.6,odds=(5.,1.2)),raw('B',p=.6,odds=(5.,1.2)))
    chosen=pick([a,b])
    assert chosen is not None and all(x['odds']==5 for x in chosen)
    c,d=prepared(raw('C',p=.54,odds=(5.,1.01)),raw('D',p=.54,odds=(5.,1.01)))
    assert pick([c,d],model='market') is None


def test_raw_votes_do_not_become_conditional_ev_probabilities():
    frames=prepared(raw('A'),raw('B'))
    assert pick(frames,model='external_raw_direction_reference',rule='forced2')
    assert pick(frames,model='external_raw_direction_reference') is None


def test_current_vote_policy_follows_vote_but_ranks_with_market_probability():
    frames=prepared(raw('A',p=.4),raw('B',p=.4))
    chosen=pick(frames,model='operating_unique_vote_market_reference',rule='forced2')
    assert chosen and all(x['selection']=='home' and x['probability']==.4 for x in chosen)
    assert pick(frames,model='operating_unique_vote_market_reference') is None


def test_current_vote_policy_abstains_on_tie():
    a,b=raw('A'),raw('B')
    for c in b['choices']:c['scores']['vote_support']=.5
    b['raw_forecasts'].append(dict(source='second',author='other',side='away',document_sha256='B2',
                                  source_reported_published_at='2026-10-29T11:00:00+09:00'))
    frames=prepared(a,b)
    assert pick(frames,model='operating_unique_vote_market_reference',rule='forced2') is None
    metric=m.forecast_metrics([r for f in frames for r in f['products']],
                             'operating_unique_vote_market_reference',params(),params())
    assert metric['events']==1 and metric['markets']==1 and metric['skipped_vote_ties']==1


def test_exact_round_and_no_shared_team():
    a,b=prepared(raw('A'),raw('B'))
    b['products'][0]['issue']=[2026,101]
    assert pick([a,b],rule='forced2') is None
    a,b=prepared(raw('A'),raw('B'))
    b['products'][0]['away']=a['products'][0]['home']
    assert pick([a,b],rule='forced2') is None


def test_primary_quote_age_and_latest_empty_no_fallback():
    a=raw('A',available='2026-10-30T16:00:00+09:00',kickoff='2026-10-30T18:30:00+09:00',tipster=False)
    f=prepared(a)[0]
    assert m.available(f,m.dt('2026-10-30T18:00:00+09:00'))
    assert not m.available(f,m.dt('2026-10-30T18:00:01+09:00'))
    latest=copy.deepcopy(a);latest['available_at']='2026-10-30T17:59:00+09:00';latest['snapshot_id']='new';latest['choices']=[]
    assert m.forecast_rosters(prepared(a,latest))==[]


def test_unknown_outcomes_do_not_change_selected_legs():
    frames=prepared(raw('A'),raw('B'))
    chosen=pick(frames)
    for f in frames:
        for c in f['products'][0]['choices']:c['hit']=None
    pending=pick(frames)
    assert [x['offer_id']for x in chosen]==[x['offer_id']for x in pending]
    assert m.settle(pending) is None


def test_rounding_void_and_component_singles():
    legs=[dict(odds=1.49,hit=True,refund=False),dict(odds=1.49,hit=True,refund=False)]
    assert m.price([1.49])==m.Decimal('1.49')
    assert m.price([1.49,1.49])==m.Decimal('2.3')
    assert m.settle(legs)==15600
    legs[1]['refund']=True
    assert m.settle(legs)==6000
    legs[0]['refund']=True
    assert m.settle(legs)==0
    legs=[dict(odds=1.49,hit=True,refund=False),dict(odds=1.3,hit=False,refund=False)]
    assert m.settle(legs,True)==-3060


def test_no_bet_has_no_roi_and_zero_cash_inclusive_return():
    sessions=[dict(day='2026-10-30',common_results_complete=True,policies={'x':dict(selected=False,stake=0,net=0,legs=[])})]
    r=m.policy_metrics(sessions,'x')
    assert r['tickets']==0 and r['roi_pct'] is None and r['session_budget_return_pct']==0


def test_two_policy_rules_share_same_first_feasible_checkpoint():
    frames=prepared(raw('A'),raw('B',available='2026-10-30T16:29:00+09:00'))
    sessions=m.ticket_sessions(frames,{'2026-10-30'},['market','fixed_market90_external10'],params(),params())
    assert len(sessions)==1
    assert sessions[0]['placement']=='2026-10-30T16:30:00+09:00'
    assert sessions[0]['common_results_complete']


def test_alias_choice_uses_numeric_issue_order_not_result():
    a=raw('A')
    later=copy.deepcopy(a['choices'])
    for c in a['choices']:c['round']=9
    for c in later:c['round']=10;c['odds']=9.9
    a['choices']=later+a['choices']
    f=prepared(a)[0]
    assert len(f['products'])==1 and f['products'][0]['issue']==[2026,9]


def test_insufficient_validation_does_not_invent_learned_weight():
    f=prepared(raw('A'))[0]['products'][0]
    train=[dict(f,event_id=str(i),product_id=str(i)) for i in range(40)]
    fit=m.fit(train,[],m.dt(f['kickoff'])+timedelta(days=3),m.dt(f['kickoff'])+timedelta(days=5))
    assert fit['temps'] and fit['weight'] is None
    assert m.predict(f,'league_shrunk_calibrated_pool',fit,fit) is None


def test_saved_final_is_not_treated_as_vote_probability():
    f=prepared(raw('A',tipster=False))[0]['products'][0]
    assert m.predict(f,'market_prior10_actual_votes',params(),params()) is None
    assert m.predict(f,'operating_unique_vote_market_reference',params(),params()) is None


def test_late_publication_excluded_independently_of_game_result():
    a=raw('A')
    a['raw_forecasts'][0]['source_reported_published_at']=a['kickoff']
    first,audit=m.prepare([a])
    assert first[0]['products']==[] and audit['known_late_publication_forecasts_excluded']==1
    a['choices'][0]['hit']=False
    assert m.prepare([a])[0][0]['products']==[]


def test_one_document_with_two_author_names_is_one_vote():
    a=raw('A')
    a['raw_forecasts'].append(dict(a['raw_forecasts'][0],author='second-name'))
    frames,audit=m.prepare([a])
    assert frames[0]['voters']==1
    assert audit['duplicate_document_forecasts_excluded']==1


def test_conflicting_document_directions_do_not_create_two_votes():
    a=raw('A')
    a['raw_forecasts'].append(dict(a['raw_forecasts'][0],author='second-name',side='away'))
    frames,audit=m.prepare([a])
    assert not frames[0]['products'] and audit['conflicting_document_groups_excluded']==1


def test_source_sha256_is_same_document_alias_for_deduplication():
    a=raw('A')
    row=a['raw_forecasts'][0];row['source_sha256']=row.pop('document_sha256')
    a['raw_forecasts'].append(dict(row,author='other',source_url='other-url'))
    frames,audit=m.prepare([a])
    assert frames[0]['voters']==1 and audit['duplicate_document_forecasts_excluded']==1


def test_minimum_training_count_is_events_not_market_copies():
    f=prepared(raw('A'))[0]['products'][0]
    rows=[dict(f,event_id=str(i//3),product_id=str(i)) for i in range(30)]
    fit=m.fit(rows,[],m.dt(f['kickoff']),m.dt(f['kickoff']))
    assert fit['train_n']==30 and fit['train_events']==10 and fit['temps']=={}


def test_missing_final_roster_does_not_change_common_model_support():
    a=raw('A',tipster=False)
    for c in a['choices']:c['scores'].pop('saved_final_reference')
    assert not prepared(a)[0]['products']


def test_claimed_time_sensitivity_never_uses_forecast_before_claimed_posting():
    a=raw('A')
    a['raw_forecasts'][0]['source_reported_published_at']='2026-10-30T17:45:00+09:00'
    frames,audit=m.claimed_time_frames([a])
    assert frames[0]['available_at']=='2026-10-30T17:45:00+09:00'
    assert not frames[0]['availability_proven']
    assert a['available_at']=='2026-10-30T16:00:00+09:00'


def test_claimed_time_sensitivity_excludes_unknown_and_after_T30():
    a,b=raw('A'),raw('B')
    a['raw_forecasts'][0].pop('source_reported_published_at')
    b['raw_forecasts'][0]['source_reported_published_at']='2026-10-30T18:30:01+09:00'
    assert m.claimed_time_frames([a,b])[0]==[]
    a['raw_forecasts'][0]['source_reported_published_at']=None
    assert m.claimed_time_frames([a])[0]==[]
