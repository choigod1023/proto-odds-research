import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parents[1]/'experiments/underdog-period-validation-20261031'
spec=importlib.util.spec_from_file_location('underdog_period_validation',HERE/'runner.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def target(day='2024-04-01T12:00:00+00:00',event='1'):
    return dict(league='MLB',event_id=event,cohort='fixture',actual_votes=False,price_evidence='test',
                home='A',away='B',sides=['home','away'],market_p=[.6,.4],odds=[1.5,2.2],outcome=1,
                decision_at=day,kickoff=day)


def history(day,hg=4,ag=2):
    return dict(league='MLB',date=day,home='A',away='B',hg=hg,ag=ag)


def test_market_ties_not_mislabeled_favorite_or_under():
    assert r.classify(1,[.5,.5])=='top_tie'
    assert r.classify(2,[.4,.4,.2])=='underdog'
    row=r.pack({**target(),'market_p':[.5,.5]},[.4,.6],'frozen')
    assert r.metrics([row],'score_favorites')['bets']==0
    assert r.metrics([row],'score_underdogs')['bets']==0
    assert r.metrics([row],'score_all')['bets']==1


def test_empty_and_no_bet_denominators_not_zero_roi():
    assert r.metrics([],'score_all')['turnover_roi'] is None
    row=r.pack(target(),[.4,.6],'frozen')
    m=r.metrics([row],'score_favorites')
    assert m['bets']==0 and m['turnover_roi'] is None and m['net_per_opportunity']==0
    assert r.paired([],True)['ci95']==[None,None]


def test_void_refunds_stake_excluded_from_hit_rate():
    row=r.pack(target(),[.4,.6],'frozen',void=True)
    m=r.metrics([row],'score_all')
    assert m['bets']==1 and m['nonvoid_bets']==0 and m['hit_rate'] is None
    assert Decimal(m['net_units'])==0 and m['turnover_roi']==0


@pytest.mark.parametrize('odds',[[float('nan'),2.],[-1,2.],[1,2.]])
def test_invalid_price_fails_closed(odds):
    with pytest.raises(ValueError):r.payout(0,0,odds)


def test_higher_roi_can_mean_larger_absolute_loss():
    # Two favorites lose one unit each. Two underdogs lose one and win .5:
    # net worsens -2 -> -2.5 even though ROI improves -100% -> -62.5%.
    rows=[r.pack({**target(event=str(i)),'outcome':1,'odds':[1.5,1.5]},[.7,.3],'frozen') for i in range(2)]
    rows.extend([r.pack({**target(event='u1'),'outcome':0,'odds':[1.5,1.5]},[.3,.7],'frozen'),r.pack({**target(event='u2'),'odds':[1.5,1.5]},[.3,.7],'frozen')])
    a=r.metrics(rows,'score_all');f=r.metrics(rows,'score_favorites')
    assert a['turnover_roi']>f['turnover_roi']
    assert Decimal(a['net_units'])<Decimal(f['net_units'])


def test_daily_close_drawdown_and_month_unused_cash():
    rows=[r.pack(target('2024-04-01T12:00:00+00:00','1'),[.3,.7],'frozen'),
          r.pack({**target('2024-04-01T13:00:00+00:00','2'),'outcome':0},[.3,.7],'frozen'),
          r.pack({**target('2024-05-01T12:00:00+00:00','3'),'outcome':0},[.7,.3],'frozen'),
          r.pack({**target('2024-06-01T12:00:00+00:00','4'),'outcome':0},[.3,.7],'frozen')]
    m=r.metrics(rows,'score_underdogs')
    assert m['monthly_net_units']=={'2024-04':.2,'2024-05':0.,'2024-06':-1.}
    assert m['max_daily_close_drawdown_units']==1.


def test_old_year_excludes_current_future_labels_and_does_not_mutate():
    h=[history('2023-10-01'),history('2024-01-01'),history('2024-03-01'),history('2025-01-01')]
    targets=[target(),target('2024-10-01T12:00:00+00:00','later')]
    a,audit=r.replay_older(h,targets)
    changed=copy.deepcopy(h)
    for item in changed[1:]:item['hg']=0;item['ag']=99
    b,audit_b=r.replay_older(changed,targets)
    assert a==b and audit==audit_b
    assert audit[0]['training_rows']==1 and audit[0]['frozen_state_preserved']
    engine=r.base_module().Engine([h[0]],'MLB','score',0.)
    engine.advance(r.at('2024-01-05T00:00:00Z'))
    assert a[0]['probabilities']==engine.predict('A','B',r.at(targets[0]['decision_at']),online=False)[0]
    # Included prior-year labels must actually influence the prediction.
    changed[0]['hg']=0;changed[0]['ag']=9
    c,_=r.replay_older(changed,targets)
    assert c[0]['probabilities']!=a[0]['probabilities']


def test_prior_to_freeze_is_excluded_and_history_datetime_rejected():
    records,audit=r.replay_older([history('2023-12-31')],[target('2024-01-04T23:59:59+00:00')])
    assert not records and audit[0]['excluded_before_freeze']==['1']
    with pytest.raises(ValueError,match='date-only'):
        r.replay_older([history('2023-12-31T23:00:00+09:00')],[target()])
    with pytest.raises(ValueError,match='timezone'):r.at('2024-01-05T00:00:00')


def test_frozen_discovery_reproduced_and_period_membership_disjoint():
    rows=[x for x in r.read(HERE/'ledger.jsonl.gz') if x['cohort']=='actual_saved_quote' and x['year']==2026 and x['mode']=='frozen']
    assert len(rows)==445 and len({x['id'] for x in rows})==445
    early=[x for x in rows if x['period']=='earlier'];late=[x for x in rows if x['period']=='discovery']
    assert len(early)==323 and len(late)==122
    assert not {x['id'] for x in early}&{x['id'] for x in late}
    assert r.metrics(late,'score_all')['net_units']=='-8.44'
    assert r.metrics(late,'score_favorites')['net_units']=='-7.68'
    assert r.metrics(late,'score_underdogs')['bets']==33
    assert r.metrics(late,'score_underdogs')['net_units']=='-0.76'


def test_independent_full_ledger_and_aggregates():
    spec=importlib.util.spec_from_file_location('underdog_independent_verifier',HERE/'verify.py')
    v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
    out=v.verify()
    assert out['ledger_rows']==1403 and out['mismatches']==0
    assert out['raw_price_settlements_checked']==2806 and out['aggregate_policy_cells_checked']>300
