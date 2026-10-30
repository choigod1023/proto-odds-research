import importlib.util
from pathlib import Path
from datetime import timedelta

spec = importlib.util.spec_from_file_location('single_parlay', Path(__file__).parents[1]/'scripts/single_parlay_research.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def frame(event='A', kickoff='2026-10-30T19:00:00+09:00', available='2026-10-30T17:00:00+09:00', **kwargs):
    c = dict(offer_id=event, selection='home', odds=1.49, scores={'market': .7},
             probability_models=['market'], issue_year=2026, round=100, game_no=1,
             hit=True, refund=False)
    out = dict(cohort='test', league='KBO', event_id=event, snapshot_id=event+available,
               kickoff=kickoff, available_at=available, home=event+'home', away=event+'away',
               choices=[c], probability_models=['market'], availability_proven=True)
    out.update(kwargs)
    return out


def choose(frames, strategy='low_parlay_2', model='market', ranking='model_score'):
    return m.best_ticket({f['event_id']: f for f in frames}, model, strategy, ranking,
                         m.dt('2026-10-30T17:00:00+09:00'), (2026,100))


def test_round_entire_product_not_individual_legs():
    assert m.quote([{'odds':1.49}]) == m.Decimal('1.49')
    assert m.quote([{'odds':1.49},{'odds':1.49}]) == m.Decimal('2.3')
    # 1.28^2=1.6384 -> 1.63 -> 1.7
    assert m.quote([{'odds':1.28},{'odds':1.28}]) == m.Decimal('1.7')


def test_refund_partial_still_parlay_rounding():
    legs=[{'odds':1.49,'hit':True,'refund':False},{'odds':1.3,'hit':False,'refund':True}]
    assert m.settle(legs)['net'] == 6000
    assert m.settle([dict(x,refund=True) for x in legs])['net'] == 0


def test_equal_budget_components_use_individual_prices():
    legs=[{'odds':1.49,'hit':True},{'odds':1.3,'hit':False}]
    assert m.settle(legs)['net'] == -12000
    assert m.settle(legs,components=True)['net'] == -3060


def test_pending_not_replaced_by_settled_loser():
    a,b,c=frame('A'),frame('B'),frame('C')
    a['choices'][0]['hit']=None
    c['choices'][0]['scores']['market']=.6
    selected=choose([a,b,c])
    assert {x['event_id'] for x in selected} == {'A','B'}
    assert m.settle(selected)['net'] is None
    a['choices'][0]['hit']=False
    assert [x['selection_key'] for x in choose([a,b,c])] == [x['selection_key'] for x in selected]


def test_no_shared_team_or_same_event():
    a,b,c=frame('A'),frame('B'),frame('C')
    b['away']=a['home']
    assert {x['event_id'] for x in choose([a,b,c])} == {'A','C'}
    assert choose([a,b]) is None


def test_no_future_quotes_and_minimum_30_minutes():
    a,b=frame('A'),frame('B',available='2026-10-30T17:01:00+09:00')
    assert choose([a,b]) is None
    b=frame('B',kickoff='2026-10-30T17:29:59+09:00')
    assert choose([a,b]) is None
    b['kickoff']='2026-10-30T17:30:00+09:00'
    assert choose([a,b]) is not None


def test_issue_round_not_crossed():
    a,b=frame('A'),frame('B')
    b['choices'][0]['round']=101
    assert choose([a,b]) is None


def test_latest_missing_model_no_old_fallback():
    a=frame('A')
    latest=frame('A',available='2026-10-30T16:59:00+09:00',choices=[])
    a['available_at']='2026-10-30T16:58:00+09:00'
    b=frame('B')
    sessions,_=m.build_sessions([a,latest,b])
    assert not any('low_parlay_2' in s['tickets'] for s in sessions)


def test_vote_zero_and_ev_unsupported():
    a,b=frame('A'),frame('B')
    for f in (a,b):
        f['choices'][0]['scores']={'vote_support':0}
    assert choose([a,b], model='vote_support') is None
    for f in (a,b):
        f['choices'][0]['scores']['vote_support']=1
    assert choose([a,b],model='vote_support') is not None
    assert choose([a,b],model='vote_support',ranking='estimated_ev') is None


def test_common_checkpoint_and_equal_budgets():
    a,b=frame('A'),frame('B')
    for f in (a,b):
        f['choices'].append(dict(f['choices'][0],offer_id=f['event_id']+'high',odds=3.1,
                                scores={'market':.25},selection='away'))
    sessions,_=m.build_sessions([a,b])
    pair=next(s for s in sessions if s['context']=='pair:single_long_3_5:low_parlay_2' and s['ranking']=='model_score')
    assert len({t['placement'] for t in pair['tickets'].values()})==1
    assert {t['stake'] for t in pair['tickets'].values()}=={12000}


def test_outcome_blind_full_sessions():
    a,b=frame('A'),frame('B')
    first,_=m.build_sessions([a,b])
    for f in (a,b):
        f['choices'][0]['hit']=False
    second,_=m.build_sessions([a,b])
    def ids(sessions):
        return [(s['context'],s['model'],s['ranking'],[(n,[x['selection_key'] for x in t['legs']]) for n,t in s['tickets'].items()]) for s in sessions]
    assert ids(first)==ids(second)


def test_high_bounds_and_low_inclusive():
    a=frame('A')
    for odds,expected in ((2.99,False),(3,True),(4.99,True),(5,False)):
        a['choices'][0]['odds']=odds
        assert bool(choose([a],strategy='single_long_3_5'))==expected
    a['choices'][0]['odds']=1.5
    assert choose([a],strategy='low_single') is not None


def test_drawdown_uses_daily_close():
    rows=[dict(day=f'2026-10-{d:02}',net=n,stake=12000,hit=n>0,refunded=False) for d,n in [(1,6000),(2,-12000),(3,-12000),(4,36000)]]
    result=m.metrics(rows)
    assert result['daily_close_max_drawdown_krw']==24000
    assert result['max_losing_ticket_streak']==2
    assert result['roi_pct']==37.5


def test_summary_opportunity_strategies_not_mixed():
    sessions,_=m.build_sessions([frame('A'),frame('B')])
    summaries,_=m.summarize(sessions)
    assert summaries
    assert all(len(s['strategies'])==1 for s in summaries if s['context'].startswith('opportunity:'))


def test_models_compared_only_at_common_time():
    a,b=frame('A'),frame('B')
    for f in (a,b):
        f['choices'].append(dict(f['choices'][0],offer_id=f['event_id']+'hi',odds=3.2,
                                scores={'market':.2},selection='away'))
    latest=frame('B',available='2026-10-30T17:30:00+09:00')
    latest['choices']= [dict(c, scores=dict(c['scores'],other=.5)) for c in b['choices']]
    latest['probability_models'].append('other')
    a['choices']=[dict(c,scores=dict(c['scores'],other=.5)) for c in a['choices']]
    a['probability_models'].append('other')
    sessions,_=m.build_sessions([a,b,latest])
    common=[s for s in sessions if s['context']=='model_common:pair:single_long_3_5:low_parlay_2' and s['ranking']=='model_score']
    assert {s['model'] for s in common}=={'market','other'}
    assert {t['placement'] for s in common for t in s['tickets'].values()}=={'2026-10-30T17:30:00+09:00'}


def test_different_model_sets_never_pooled():
    a=frame('A')
    t=m.ticket(choose([a],strategy='low_single'),'low_single',m.dt(a['available_at']))
    base=dict(cohort='x',league='KBO',day='2026-10-30',issue=[2026,100],model='market',
              ranking='model_score',context='model_common:example',tickets={'low_single':t})
    summaries,_=m.summarize([dict(base,models_compared=['market','score']),
                             dict(base,models_compared=['market','score','hybrid'])])
    assert len(summaries)==2
    assert all(x['strategies']['low_single']['n']==1 for x in summaries)


def test_missing_issued_game_number_ineligible_regardless_of_outcome():
    a,b=frame('A'),frame('B')
    a['choices'][0]['game_no']=None
    assert choose([a,b]) is None
    a['choices'][0]['hit']=False
    assert choose([a,b]) is None
