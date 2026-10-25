from datetime import datetime, timedelta
import pytest
from scripts.replay_tipster_opinions import ET, canonical, prepare, opportunities, simulate, payout


def raw_pair():
    first = dict(pick_id='pre',event_type='first_observed',identity_version=2,
        eligible_pre_event=True,game_started=False,result=None,is_parlay_leg=False,
        observed_at='2026-08-29T10:00:00Z',slate_date='2026-08-29',team_a='BOS',team_b='NYY',
        slug='alice',market_type='moneyline',selection='NYY ML -115 5u',american_odds=-115,
        game_header='BOS 1:05 PM ET NYY Pitcher A vs Pitcher B')
    last = dict(first,pick_id='post',event_type='first_observed',eligible_pre_event=False,
        game_started=True,result='W',observed_at='2026-08-29T22:00:00Z',selection='NYY ML -115')
    return first,last


def test_relink_original_quote_only():
    a,b=raw_pair()
    pool,_=prepare([b,a])
    assert canonical(a)==canonical(b)
    assert pool[0]['outcome']=='W'
    assert pool[0]['odds']==pytest.approx(1+100/115)
    b['selection']='NYY ML -120'
    assert prepare([a,b])[0][0]['outcome'] is None


def test_earlier_and_conflicting_final_stays_unresolved():
    a,b=raw_pair()
    early=dict(b,observed_at='2026-08-29T05:00:00Z',pick_id='old')
    assert prepare([a,b,early])[0][0]['outcome'] is None
    assert prepare([a,b,dict(b,result='L')])[0][0]['outcome'] is None


def test_no_backfilled_odds():
    a,b=raw_pair()
    a['american_odds']=None
    assert prepare([a,b])[0]==[]


def candidate(day,event,author='a',side='HOME',odds=1.8,outcome='L',settled=True):
    d=datetime(2026,8,day,10,tzinfo=ET)
    return dict(id=f'{day}-{event}-{author}',event=f'{day}-{event}',author=author,side=side,
        date=d.date().isoformat(),observed=d,kickoff=d.replace(hour=18),odds=odds,
        outcome=outcome,settled=d.replace(hour=23) if settled else None)


def test_future_opinion_and_schedule_do_not_change_decision():
    r=candidate(1,'A')
    now=r['observed'].replace(hour=12)
    future=dict(r,id='future',observed=now+timedelta(hours=2),kickoff=now+timedelta(hours=8))
    assert opportunities([r,future],now,'lower_odds')==opportunities([r],now,'lower_odds')


def test_one_author_vote_and_tie():
    rows=[candidate(1,'A',a,side=s) for a,s in [('a','HOME'),('b','HOME'),('c','AWAY')]]
    now=rows[0]['observed'].replace(hour=12)
    assert opportunities(rows+[dict(rows[0],id='duplicate')],now,'consensus')[0]['total_votes']==3
    assert not opportunities(rows+[candidate(1,'A','d',side='AWAY')],now,'consensus')


def test_missing_results_do_not_filter_choices():
    rows=[candidate(1,'A',odds=1.5,outcome=None,settled=False),candidate(1,'B',odds=1.6,outcome=None,settled=False),candidate(1,'C',odds=2)]
    result=simulate(rows,datetime(2026,8,2,tzinfo=ET),'lower_odds',3)
    assert result['tickets']==1 and result['pending']==1
    assert result['pending_stake_krw']==10000
    assert result['worst_case_net_krw']==-10000
    assert result['ticket_log'][0]['events']==['1-A','1-B']


def test_month_loss_and_weekly_caps():
    rows=[candidate(day,e) for day in range(1,29) for e in ('A','B')]
    for weekly in (2,3):
        result=simulate(rows,datetime(2026,8,31,tzinfo=ET),'lower_odds',weekly)
        assert result['tickets']==5
        assert result['net_krw']==-50000


def test_pending_stakes_consume_budget():
    rows=[candidate(day,e,outcome=None,settled=False) for day in range(1,29) for e in ('A','B')]
    result=simulate(rows,datetime(2026,8,31,tzinfo=ET),'lower_odds',3)
    assert result['tickets']==5 and result['pending']==5
    assert result['net_krw']==0 and result['worst_case_net_krw']==-50000


def test_rounding_void_and_loss():
    assert payout([dict(odds=1.49,outcome='W'),dict(odds=1,outcome='V')])==15000
    assert payout([dict(odds=1.5,outcome='W'),dict(odds=1.5,outcome='W')])==23000
    assert payout([dict(odds=1.5,outcome='W'),dict(odds=1.5,outcome='L')])==0


def test_losing_leg_settles_without_other_result():
    rows=[candidate(1,'A'),candidate(1,'B',outcome=None,settled=False)]
    result=simulate(rows,datetime(2026,8,2,tzinfo=ET),'lower_odds',2)
    assert result['net_krw']==-10000 and result['pending']==0
