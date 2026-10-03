from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('model_review', Path(__file__).resolve().parents[1]/'scripts/model_review.py')
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def fixture():
    matches = [dict(match_id=str(i), league='D1', date='2024-01-01', y=0, prices=[2,3,4]) for i in range(2)]
    legs = [dict(match_id=str(i), outcome=0, odds=2) for i in range(2)]
    record = dict(date='2024-01-01', league='D1', budget=1, stake=1, won=1, profit=3., legs=legs)
    return dict(folds=[dict(outer_year=2023, forecasts=matches,
        ledgers={n:dict(highestprob=[deepcopy(record)]) for n in ('shin','candidate')})])


def test_release_boundary_and_no_future_inspection():
    data = fixture()
    assert not review.extract(data,['shin','candidate'],'highestprob','2024-01-07')['shin']
    assert len(review.extract(data,['shin','candidate'],'highestprob','2024-01-08')['shin']) == 1
    data['folds'][0]['ledgers']['candidate']['highestprob'][0]['profit'] = float('nan')
    assert not review.extract(data,['shin','candidate'],'highestprob','2024-01-07')['candidate']
    with pytest.raises(ValueError,match='settlement'):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


@pytest.mark.parametrize('field,value', [('profit',4),('stake',0),('won',0),('budget',2)])
def test_recomputes_settlement(field,value):
    data=fixture()
    data['folds'][0]['ledgers']['candidate']['highestprob'][0][field]=value
    with pytest.raises(ValueError):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


def test_missing_cash_budget_rejected():
    data=fixture()
    data['folds'][0]['ledgers']['candidate']['highestprob']=[]
    with pytest.raises(ValueError,match='unpaired'):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


def test_duplicate_budget_rejected():
    data=fixture()
    records=data['folds'][0]['ledgers']['candidate']['highestprob']
    records.append(deepcopy(records[0]))
    with pytest.raises(ValueError,match='duplicate'):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


def test_both_models_cannot_silently_omit_same_budget():
    data=fixture()
    for policies in data['folds'][0]['ledgers'].values():
        policies['highestprob']=[]
    with pytest.raises(ValueError,match='source budgets'):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


def test_same_match_parlay_rejected():
    data=fixture()
    legs=data['folds'][0]['ledgers']['candidate']['highestprob'][0]['legs']
    legs[1]=deepcopy(legs[0])
    with pytest.raises(ValueError,match='distinct'):
        review.extract(data,['shin','candidate'],'highestprob','2024-01-08')


def test_drawdown_aggregates_same_day_cash_does_not_reset_losing_run():
    rows=[dict(date=d,profit=p,stake=s,won=int(p>0)) for d,p,s in
          [('2024-01-01',-1,1),('2024-01-01',3,1),('2024-01-02',-1,1),
           ('2024-01-03',0,0),('2024-01-04',-1,1)]]
    measured=review.metrics(rows)
    assert measured['max_drawdown_units']==2
    assert measured['longest_losing_active_days']==2
    assert measured['roi']==0
    assert review.metrics([])['roi'] is None


def test_small_positive_sample_never_promotes_or_mutates():
    data=fixture()
    before=deepcopy(data)
    result=review.review(data,'shin',['candidate'],'highestprob','2024-01-08')
    assert data==before
    assert result['production_action']=='none' and result['approval_required']
    assert result['candidates']['candidate']['status']=='hold'
    assert not result['candidates']['candidate']['checks']['minimum_sample']


def test_identical_models_have_zero_paired_difference():
    from datetime import date,timedelta
    rows=[dict(date=(date(2024,1,1)+timedelta(days=7*i)).isoformat(),stake=1,profit=1 if i%2 else -1)
          for i in range(40)]
    result=review.bounds(rows,rows,comparisons=1,looks=1)
    assert result['improvement_lower']==0
    strict=review.bounds(rows,rows,comparisons=4,looks=100)
    assert strict['roi_lower'] is None  # insufficient bootstrap tail resolution


@pytest.mark.parametrize('candidates,looks,minimum', [([],1,300),(['shin'],1,300),(['x','x'],1,300),(['x'],0,300),(['x'],1,299)])
def test_invalid_policy_rejected(candidates,looks,minimum):
    with pytest.raises(ValueError):
        review.review(fixture(),'shin',candidates,'highestprob','2024-01-08',planned_looks=looks,minimum_tickets=minimum)


def test_even_statistical_success_stays_research_only(monkeypatch):
    from datetime import date,timedelta
    rows=[dict(date=(date(2023,1,1)+timedelta(days=i)).isoformat(),league='D1',season=2023+i//200,
               profit=1,stake=1,won=1) for i in range(400)]
    baseline=[dict(r,profit=.5) for r in rows]
    monkeypatch.setattr(review,'extract',lambda *a: {'shin':baseline,'candidate':rows})
    monkeypatch.setattr(review,'bounds',lambda *a,**k:dict(weeks=58,roi_lower=.8,improvement_lower=.4))
    result=review.review({},'shin',['candidate'],'highestprob','2025-01-01')
    assert result['candidates']['candidate']['historical_screen_passed']
    assert result['candidates']['candidate']['status']=='research_followup_only'
    assert result['production_action']=='none'


def test_replay_never_uses_future_decision_for_earlier_ticket(monkeypatch):
    calls=[]
    def screen(artifact,champion,candidates,policy,as_of,**kwargs):
        calls.append((as_of,kwargs['planned_looks']))
        return {'candidates':{'candidate':dict(historical_screen_passed=as_of>='2024-02-01',bounds={'improvement_lower':.1})}}
    rows=[dict(date=d,stake=1,won=0,profit=-1) for d in ['2024-01-15','2024-02-01','2024-02-15']]
    monkeypatch.setattr(review,'review',screen)
    monkeypatch.setattr(review,'extract',lambda *a:dict(shin=rows,candidate=[dict(r,profit=2,won=1) for r in rows]))
    result=review.replay({},'shin',['candidate'],'highestprob',['2024-01-01','2024-02-01'],'2024-03-01')
    assert calls==[('2024-01-01',2),('2024-02-01',2)]
    assert [r['selected_model'] for r in result['ledger']]==['shin','candidate','candidate']
    assert result['selected_metrics']['profit']==3
    assert result['production_action']=='none'


def test_replay_future_outcome_mutation_preserves_prior_decision():
    data=fixture()
    first=review.replay(data,'shin',['candidate'],'highestprob',['2023-12-31'],'2024-01-10')
    for row in data['folds'][0]['forecasts']:
        row['y']=1
    for policies in data['folds'][0]['ledgers'].values():
        policies['highestprob'][0].update(won=0,profit=-1)
    second=review.replay(data,'shin',['candidate'],'highestprob',['2023-12-31'],'2024-01-10')
    assert first['decisions']==second['decisions']
    assert first['selected_metrics']['profit']!=second['selected_metrics']['profit']


def test_replay_invalid_dates_rejected():
    with pytest.raises(ValueError):
        review.replay({},'shin',['candidate'],'highestprob',['2024-02-01','2024-01-01'],'2024-03-01')
