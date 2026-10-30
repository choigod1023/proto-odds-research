from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import pregame_rd_compare as m


def case(i=0,*,hour=10):
    return dict(cohort='test',event_id=f'e{i}',league='MLB',home=f'h{i}',away=f'a{i}',
        kickoff=f'2026-10-30T{hour:02}:00:00+09:00',decision_at=f'2026-10-30T{hour-1:02}:30:00+09:00',
        available_at='2026-10-29T00:00:00+09:00',day='2026-10-30',issue=[2026,100],game_no=str(i+1),
        sides=['home','away'],market_p=[.7,.3],external_p=[1.,0.],odds=[1.6,2.5],outcome=0,void=False,
        auxiliary=False,product_id=f'p{i}',features={'x':float(i%5)})


def test_multiclass_objective_gradient_matches_finite_difference():
    rng=np.random.default_rng(123)
    for k in (2,3):
        X=np.column_stack((np.ones(7),rng.normal(size=(7,2))));y=np.asarray([i%k for i in range(7)])
        offset=rng.normal(size=(7,k));theta=rng.normal(size=3*(k-1))
        loss,gradient=m.objective(theta,X,y,offset,k)
        numeric=[]
        for i in range(len(theta)):
            delta=np.zeros_like(theta);delta[i]=1e-6
            numeric.append((m.objective(theta+delta,X,y,offset,k)[0]-m.objective(theta-delta,X,y,offset,k)[0])/2e-6)
        assert np.allclose(gradient,numeric,atol=1e-6)


def test_training_only_preprocessing_and_missing_flags():
    train=[case(i)for i in range(5)];prep=m.preprocessing(train)
    before=dict(prep)
    test=case(99);test['features']={'x':1e12}
    matrix=m.matrix([test],prep)
    assert prep==before and matrix[0,1]==5
    test['features']={};matrix=m.matrix([test],prep)
    assert matrix[0,1]==0 and matrix[0,2]==1


def test_heldout_label_changes_cannot_change_parameters_or_predictions():
    train=[case(i)for i in range(50)]
    for r in train:r['outcome']=int(r['features']['x']<2)
    heldout=[case(i+50)for i in range(10)]
    fitted=m.fit_model(train);before=m.predict(heldout,fitted)
    for r in heldout:r['outcome']=1-r['outcome']
    repeated=m.fit_model(train)
    assert fitted==repeated and before==m.predict(heldout,repeated)


def test_fixed_single_eligibility_does_not_fill_or_cap_prices():
    rows=[case(i)for i in range(3)]
    rows[2]['odds'][0]=4.
    result=m.eligible_single_metrics(rows,[[.54,.46],[.6,.4],[.7,.3]])
    assert result['available']==3 and result['selected']==1 and result['n']==1
    assert result['roi_pct']==300.


def test_fit_requires_independent_events_and_finite_probabilities():
    duplicate=[case(1)for _ in range(40)]
    assert m.fit_model(duplicate) is None
    rows=[case(i)for i in range(50)]
    for r in rows:r['outcome']=int(r['features']['x']<2)
    model=m.fit_model(rows)
    assert model['status']=='fitted'
    p=np.asarray(m.predict(rows,model))
    assert np.isfinite(p).all() and np.allclose(p.sum(axis=1),1)


def test_canonical_order_keeps_price_probability_and_target_aligned():
    raw=dict(cohort='retrospective',event_id='x',league='MLB',home='H',away='A',
        kickoff='2026-10-30T10:00:00+09:00',available_at='2026-10-29T10:00:00+09:00',
        market='full_game_moneyline',issue=[2026,100],game_no='1',product_id='p',
        choices=[{'label':'away'},{'label':'home'}],market_p=[.2,.8],external_p=[0.,1.],
        odds=[4.,1.1],target=1,void=False,tipster=True)
    r=m.canonical_roster(raw)
    assert r['market_p']==[.8,.2] and r['odds']==[1.1,4.] and r['outcome']==0
    assert r['external_p']==[1.,0.]


def test_no_fill_two_leg_and_no_odds_ceiling():
    rows=[case(0),case(1)]
    result=m.two_leg(rows,[[.7,.3],[.5,.5]])
    assert result['opportunities']==1 and result['tickets']==0 and result['roi_pct'] is None
    rows[0]['odds'][0]=4.;rows[1]['odds'][0]=4.
    assert m.two_leg(rows,[[.7,.3],[.7,.3]])['tickets']==1


def test_no_same_team_and_no_future_per_game_freeze_in_pair():
    a,b=case(0),case(1,hour=11)
    assert m.two_leg([a,b],[[.7,.3],[.7,.3]])['opportunities']==0
    b=case(1);b['away']=a['home']
    assert m.two_leg([a,b],[[.7,.3],[.7,.3]])['tickets']==0


def test_explicit_team_aliases_cannot_create_a_second_leg():
    a,b=case(0),case(1)
    a.update(league='NBA',home='LA Clippers',away='Boston Celtics')
    b.update(league='NBA',home='Los Angeles Clippers',away='New York Knicks')
    assert m.two_leg([a,b],[[.7,.3],[.7,.3]])['tickets']==0


def test_ambiguous_fixture_aliases_fail_closed_and_doubleheader_times_stay_distinct():
    a,b=case(0),case(1);b.update(home=a['home'],away=a['away'])
    rows,audit=m.deduplicate_cases([a,b])
    assert rows==[] and audit['ambiguous_physical_alias_rows_excluded']==2
    a['official_game_id']=b['official_game_id']='verified-123'
    assert len(m.deduplicate_cases([a,b])[0])==1
    b['official_game_id']='verified-124'
    b['kickoff']='2026-10-30T14:00:00+09:00'
    assert len(m.deduplicate_cases([a,b])[0])==2


def test_direction_only_reference_uses_market_probability_for_two_leg():
    rows=[case(0),case(1)]
    # Vote directions can be away even when home is the market favorite. A
    # one-hot vote is not used to fabricate p=1 or a positive EV.
    result=m.two_leg(rows,[[.7,.3],[.7,.3]],[1,1])
    assert result['tickets']==0


def test_late_input_cannot_form_a_ticket():
    rows=[case(0),case(1)]
    rows[1]['available_at']='2026-10-30T09:59:00+09:00'
    assert m.two_leg(rows,[[.7,.3],[.7,.3]])['tickets']==0


def test_void_outcome_is_not_graded_and_paired_roi_uses_refund_denominator():
    a,b=case(0),case(1);a['void']=True
    p=[[1.,0.],[1.,0.]];q=[[0.,1.],[0.,1.]]
    lhs=m.metrics([a,b],p);rhs=m.metrics([a,b],q)
    delta=m.paired_delta([a,b],p,q)
    assert lhs['graded']==1 and rhs['graded']==1 and delta['graded']==1
    assert delta['roi_delta_pp']==pytest.approx(lhs['roi_pct']-rhs['roi_pct'])
    assert delta['accuracy_delta_pp']==100.


def test_actual_pair_rounding_and_refund():
    a,b=case(0),case(1);a['odds'][0]=b['odds'][0]=1.49
    out=m.pair_settlement([(a,[.7,.3],0),(b,[.7,.3],0)])
    assert out['quoted_odds']==2.3 and out['net_krw']==15600
    b['void']=True;b['outcome']=None
    assert m.pair_settlement([(a,[.7,.3],0),(b,[.7,.3],0)])['net_krw']==6000
    a['outcome']=None
    assert m.pair_settlement([(a,[.7,.3],0),(b,[.7,.3],0)]) is None


def test_evidence_gate_does_not_use_unknown_current_xi(tmp_path):
    r=case(0)
    f=dict(cohort='test',event_id='e0',kickoff=r['kickoff'],decision_at=r['decision_at'],features={'xi':11},
           feature_evidence={'xi':dict(max_fact_time=r['available_at'],observed_at=None,
                source_sha256=['a'*64],availability_tier='unverified')})
    index={('soccer','test','e0',r['kickoff']):[f]}
    rows,audit=m.attach([r],index,'soccer','retrospective_lagged',{})
    assert rows==[] and audit['field:unverified_feature_tier']==1
