import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('selection',Path(__file__).parents[1]/'scripts/compare_selection_policies.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(i,day=1,y=0):
    return dict(id=str(i),event=str(i),at=f'2025-01-{day:02d}T12:00:00+00:00',
                kickoff=f'2025-01-{day:02d}T14:00:00+00:00',snapshot_at=f'2025-01-{day:02d}T11:00:00+00:00',
                model=[.7,.3],tipster=[.6,.4],odds=[1.6,2.5],y=y,
                settled_at=f'2025-01-{day:02d}T17:00:00+00:00' if y is not None else None)


def test_result_does_not_change_recommendations_or_pair():
    a=[row(1),row(2),row(3)]
    b=[dict(r,y=None,settled_at=None) for r in a]
    for policy in m.POLICIES:
        ra=m.recommend(a,'hybrid75_25',policy);rb=m.recommend(b,'hybrid75_25',policy)
        assert [(r['id'],r['side']) for r in ra]==[(r['id'],r['side']) for r in rb]
        assert [r['id'] for r in m.select_pair(ra,policy)]==[r['id'] for r in m.select_pair(rb,policy)]


def test_positive_edge_excludes_negative_edge():
    r=dict(row(1),model=[.55,.45],odds=[1.5,2.0])
    assert len(m.recommend([r],'model','probability'))==1
    assert m.recommend([r],'model','edge')==[]


def test_no_forced_bet_and_unknown_roi():
    r=m.replay_tickets([], 'edge',2,m.stamp('2025-02-01T00:00:00+00:00'))
    assert r['tickets']==0 and r['roi_pct'] is None


def test_pending_reserves_budget_and_same_game_not_reused():
    rows=[row(day*10+i,day,None) for day in range(1,20) for i in (1,2)]
    r=m.replay_tickets(m.recommend(rows,'model','probability'),'probability',3,m.stamp('2025-02-01T00:00:00+00:00'))
    assert r['tickets']==5 and r['pending_stake_krw']==50000
    assert r['worst_case_net_krw']==-50000
    assert all(t['reserved_budget_before']>=10000 for t in r['ticket_log'])


def test_loss_limit_and_weekly_cap():
    rows=[row(day*10+i,day,1) for day in range(1,20) for i in (1,2)]
    r=m.replay_tickets(m.recommend(rows,'model','probability'),'probability',3,m.stamp('2025-02-01T00:00:00+00:00'))
    assert r['net_krw']==-50000 and r['tickets']==5
    assert r['max_drawdown_krw']==50000


def test_unknown_leg_and_known_loss_settle_loss():
    rows=[row(1,y=None),row(2,y=1)]
    r=m.replay_tickets(m.recommend(rows,'model','probability'),'probability',2,m.stamp('2025-02-01T00:00:00+00:00'))
    assert r['settled']==1 and r['net_krw']==-10000


def test_timing_rejected():
    rows=[dict(row(i),snapshot_at='2025-01-01T13:00:00+00:00') for i in (1,2)]
    assert m.replay_tickets(m.recommend(rows,'model','probability'),'probability',2,m.stamp('2025-02-01T00:00:00+00:00'))['tickets']==0


def test_soccer_draw_and_no_upper_cap():
    r=dict(row(1),model=[.2,.6,.2],tipster=[0,1,0],odds=[2,3,2])
    assert m.recommend([r],'hybrid75_25','edge')[0]['side']==1


def test_rounding():
    assert m.rounded_price([{'price':1.49},{'price':1.5}])==2.3


def test_two_distinct_events():
    rows=[dict(row(i),event='same') for i in (1,2)]
    assert m.select_pair(m.recommend(rows,'model','probability'),'probability')==[]


def test_different_rounds_cannot_form_ticket():
    rows=[dict(row(i),round_key=str(i)) for i in (1,2)]
    assert m.select_pair(m.recommend(rows,'model','probability'),'pair_edge')==[]


def test_weekly_two_and_daily_ticket_limit():
    rows=[row(day*10+i,day) for day in range(6,13) for i in (1,2,3,4)]
    r=m.replay_tickets(m.recommend(rows,'model','probability'),'probability',2,m.stamp('2025-02-01T00:00:00+00:00'))
    assert r['tickets']==2
    assert len({t['at'] for t in r['ticket_log']})==2
