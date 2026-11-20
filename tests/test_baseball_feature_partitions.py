from datetime import datetime,timezone
from pathlib import Path
import copy,json,sys
import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_baseball_feature_lab import pregame_features
from backtest_baseball_feature_partitions import design,objective,fit,predict,gate,choose_gate,dramatic,measure,split_dates,select
from audit_baseball_price_movement import quote_fact


def source():
    def player(i,side):return dict(id=i,pitching=dict(outs=90,earnedRuns=10+side,battersFaced=130,strikeOuts=30,
        baseOnBalls=8,hitByPitch=1,homeRuns=3,gamesStarted=5,numberOfPitches=490),
        batting=dict(atBats=100,hits=20+side,baseOnBalls=10,hitByPitch=1,sacFlies=2,totalBases=36+side,plateAppearances=115,strikeOuts=22))
    return dict(start_at='2026-05-01T01:00:00+00:00',game_type='R',home_score=2,away_score=3,
        sides={s:dict(starter=player(offset,side),lineup=[player(offset+i,side) for i in range(1,10)]) for s,offset,side in [('home',100,0),('away',200,1)]})


def test_source_score_and_current_season_savant_cannot_leak_into_features():
    r=source();tables={(2025,k):{} for k in ['pitcher','batter']};a,g=pregame_features(r,tables)
    r.update(home_score=99,away_score=0);tables[2026,'batter']={101:dict(pa=9999,est_woba=.99)}
    b,h=pregame_features(r,tables);assert a==b and g==h and g['early_season']==0


def test_all_directional_features_reverse_when_teams_are_swapped():
    r=source();tables={(2025,k):{} for k in ['pitcher','batter']};a,g=pregame_features(r,tables)
    r['sides']['home'],r['sides']['away']=r['sides']['away'],r['sides']['home'];b,h=pregame_features(r,tables)
    np.testing.assert_allclose(list(a.values()),-np.array(list(b.values())),atol=1e-12);assert g==h


def facts(n=100):
    from backtest_baseball_feature_partitions import GAP
    rows=[]
    for i in range(n):
        value=(i%9-4)/3
        rows.append(dict(gamePk=i,date=f'2024-04-{i%28+1:02d}',features={k:value for k in GAP+['legacy_log_era']},
            groups=dict(early_season=int(i%2==0),novice_starter=int(i%3==0),low_batter_sample=0,postseason=0),
            hg=int(value>0),ag=int(value<=0),historical_prices=[1.6,2.1]))
    return rows


def test_chronological_split_does_not_separate_same_day():
    train,val=split_dates(facts())
    assert max(r['date'] for r in train)<min(r['date'] for r in val)


@pytest.mark.parametrize('intercept',[True,False])
def test_logistic_gradient_with_market_offset(intercept):
    x=np.array([[1.,-.5],[1.,.3],[1.,1.2]]);y=np.array([0.,1.,1.]);offset=np.array([.3,-.2,.7]);beta=np.array([.1,.2])
    assert check_grad(lambda t:objective(t,x,y,offset,.01,intercept)[0],lambda t:objective(t,x,y,offset,.01,intercept)[1],beta)<1e-6


def test_neutral_design_preserves_symmetry_and_has_no_home_constant():
    rows=facts();m=fit(rows,'legacy7','neutral',.1)
    flipped=copy.deepcopy(rows)
    for r in flipped:r['features']={k:-v for k,v in r['features'].items()}
    np.testing.assert_allclose(predict(m,rows)+predict(m,flipped),1,atol=1e-12)
    assert np.all(np.array(m['center'])==0)


def test_zero_market_residual_is_exact_market_reference():
    rows=facts();m=fit(rows,'legacy7','market_offset',.1);m['beta']=[0.]*len(m['beta'])
    np.testing.assert_allclose(predict(m,rows),predict(None,rows),atol=1e-12)


def test_group_eligibility_is_determined_by_training_not_evaluation():
    rows=facts();m=fit(rows,'groups','neutral',.1)
    assert m['groups']==['early_season']
    probe=copy.deepcopy(rows[:2]);probe[0]['groups']['postseason']=1
    assert design(probe,m).shape[1]==len(m['beta'])


def test_selection_uses_validation_and_gate_must_retain_half():
    scores={'a':dict(validation=dict(accuracy=.55,brier=.25,logloss=.69),evaluation=dict(accuracy=1)),
            'b':dict(validation=dict(accuracy=.6,brier=.24,logloss=.67),evaluation=dict(accuracy=0))}
    assert select(scores,'accuracy')=='b' and select(scores,'logloss')=='b'
    g={'all':dict(n=100,coverage=1.,accuracy=.6,brier=.24),'tiny':dict(n=8,coverage=.08,accuracy=1.,brier=.1)}
    assert choose_gate(g)=='all'


def test_apparent_large_gain_at_low_coverage_does_not_pass_dramatic_criterion():
    a=dict(n=38,coverage=38/203,accuracy=.76,roi=.1);b=dict(accuracy=.60,roi=-.08)
    assert not dramatic(a,b,dict(accuracy_delta95=[.02,.3]))
    a['coverage']=.6;assert dramatic(a,b,dict(accuracy_delta95=[.02,.3]))
    assert not dramatic(a,b,dict(accuracy_delta95=[-.01,.3]))


def test_refund_and_same_subset_baseline():
    rows=[dict(hg=3,ag=2,date='2024-04-01',historical_prices=[1.5,2.2]),dict(hg=2,ag=2,date='2024-04-02',historical_prices=[1.5,2.2])]
    m=measure(rows,[.6,.7]);assert m['pushes']==1 and m['accuracy']==1 and m['roi']==.25
    m=measure(rows,[.6,.7],[True,False]);assert m['coverage']==.5 and m['roi']==.5


def quote(result='경기전',ts='2026-08-01T03:00:00+00:00'):
    return dict(league='KBO',market_family='승패',n_way='2',result=result,home='LG',away='KT',date_text='08.01(토) 18:00',year='2026',round='89',ts=ts,odds='1.55,2.1')


def test_postgame_rows_never_become_movement_features():
    q,reason=quote_fact(quote('홈승'),{},lambda l,n:n);assert q is None and reason=='not_pregame_source_row'


def test_movement_requires_pre_cutoff_aware_timestamp():
    targets={('KBO','2026-08-01','LG','KT'):dict()};canon=lambda l,n:n
    q,reason=quote_fact(quote(),targets,canon);assert q and reason is None
    q,reason=quote_fact(quote(ts='2026-08-01T08:50:00Z'),targets,canon);assert q is None and reason=='at_or_after_tminus10'
    q,reason=quote_fact(quote(ts='2026-08-01T03:00:00'),targets,canon);assert q is None and reason=='observation_without_zone'


def test_nonfinite_price_is_rejected_before_probability_calculation():
    r=quote();r['odds']='nan,2.1'
    q,reason=quote_fact(r,{('KBO','2026-08-01','LG','KT'):{}},lambda l,n:n)
    assert q is None and reason=='invalid_odds'


def test_market90_vote10_cannot_flip_favorite_above_five_ninths():
    for p in np.linspace(.56,.99,100):
        for share in np.linspace(0,1,30):assert .9*p+.1*share>.5
    assert .9*.55+.1*0<.5
