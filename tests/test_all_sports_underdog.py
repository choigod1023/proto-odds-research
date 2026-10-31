import copy
from datetime import date,timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import numpy as np
import pytest

HERE=Path(__file__).resolve().parents[1]/'experiments/all-sports-underdog-20261031'
def module(name):
    s=importlib.util.spec_from_file_location('allsports_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
c=module('common');p=module('prepare')


def fixtures(n=250,sport='bs'):
    rows=[]
    for i in range(n):
        day=(date(2023,1,1)+timedelta(days=i//5)).isoformat();k=3 if sport=='sc' else 2
        rows.append(dict(id=str(i),cohort='test',sport=sport,league='test',home='A',away='B',day=day,kickoff=day+'T12:00:00',odds=[2.,3.,4.] if k==3 else [1.6,2.1],y=i%k,sources=['test']))
    return rows


def test_merge_conflicts_drop_whole_fixture_and_union_sources():
    row=p.normalize('bs','KBO','2023-01-01','A','B',[1.6,2.1],0,'first')
    same={**copy.deepcopy(row),'sources':['second']}
    merged,a=p.merge([row,same]);assert len(merged)==1 and merged[0]['sources']==['first','second']
    changed={**copy.deepcopy(row),'y':1};merged,a=p.merge([row,changed]);assert merged==[] and a['conflicting_fixtures_excluded']==1


@pytest.mark.parametrize('sport',['bs','sc','bk','vl'])
def test_four_day_causal_feature_embargo_current_and_future_labels(sport):
    rows=fixtures(250,sport);_,x=c.causal_features(rows)
    changed=copy.deepcopy(rows)
    for r in changed[125:]:r['y']=(r['y']+1)%len(r['odds'])
    _,xx=c.causal_features(changed)
    # Mutation first occurs Jan26, available Jan30; all earlier features invariant.
    safe=[i for i,r in enumerate(rows) if r['day']<'2023-01-30']
    assert np.array_equal(x[safe],xx[safe])
    assert np.array_equal(x[:20],np.tile(x[0],(20,1)))
    assert c.causal_features(rows[::-1])[0]==c.causal_features(rows)[0]
    assert np.array_equal(c.causal_features(rows[::-1])[1],x)


def test_future_month_labels_never_change_frozen_month_fit():
    rows=fixtures(650);a,states=c.forecasts(rows)
    changed=copy.deepcopy(rows)
    for r in changed:
        if r['day']>='2023-03-01':r['y']=1-r['y']
    b,other=c.forecasts(changed)
    assert states[:3]==other[:3]  # Mar model fits only through Feb25.
    for old,new in zip(a,b):
        if old['day']<'2023-03-05':
            for name in c.MODELS:assert old['predictions'][name]['p']==new['predictions'][name]['p']


def test_cold_start_is_explicit_market_fallback_no_future_fill():
    rows,states=c.forecasts(fixtures(20,'sc'))
    assert all(r['fallback'] for r in rows) and states[0]['training_rows']==0
    assert all(r['predictions']['market']['p']==r['predictions']['outcome_form']['p'] for r in rows)
    assert all(abs(sum(r['predictions']['outcome_form']['p'])-1)<1e-12 for r in rows)


def test_ties_and_draw_rank_are_distinct():
    assert c.classify(0,[.4,.4,.2])=='top_tie'
    assert c.classify(2,[.4,.4,.2])=='underdog'
    assert c.classify(1,[.6,.3,.1])=='underdog'
    assert c.metrics([],'market','all')['roi'] is None


def test_same_event_market_comparison_and_turnover_denominators():
    rows,_=c.forecasts(fixtures(20))
    r=copy.deepcopy(rows[0]);r['predictions']['outcome_form'].update(group='underdog',net='-1',hit=False)
    r['predictions']['market'].update(net='0.6',hit=True)
    model=c.metrics([r],'outcome_form','underdogs');market=c.metrics([r],'outcome_form','market_same_underdogs')
    assert model['bets']==market['bets']==1 and Decimal(model['net_units'])==-1 and Decimal(market['net_units'])==Decimal('.6')
    empty=c.metrics([r],'outcome_form','favorites');assert empty['roi'] is None and empty['net_per_opportunity']==0


def test_no_invalid_source_prices_or_labels():
    with pytest.raises(ValueError):p.normalize('sc','K1','2023-01-01','A','B',[2.,2.],0,'test')
    with pytest.raises(ValueError):p.normalize('bs','KBO','2023-01-01','A','B',[1.,2.],0,'test')
    with pytest.raises(ValueError):p.normalize('bs','KBO','2023-01-01','A','B',[1.6,2.1],2,'test')
