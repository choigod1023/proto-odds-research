import importlib.util
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from expand_tipster_fixed91 import decision, link, compare_fixed, validate_official_timing, frozen_budget_tickets, input_changes
from matched_tipster_source import stamp


def sample():
    r=dict(id='a',kickoff='2026-09-01T20:00:00-04:00',snapshot_at='2026-09-01T19:00:00-04:00',
           snapshot_id='sha',home='CLE',away='DET',date='2026-09-01',model=[.54,.46],odds=[1.8,2.0],round_key='1')
    t=dict(id='tip',author='author',event='2026-09-01|CLE|DET',side='DET',
           observed=stamp('2026-09-01T19:15:00-04:00'),kickoff=stamp(r['kickoff']),outcome=None,settled=None)
    return r,t


def test_t30_expansion_is_not_noon_equivalent():
    r,t=sample()
    assert link([r],[t],'noon')[0]==[]
    rows,_=link([r],[t],'t30')
    assert len(rows)==1 and rows[0]['y'] is None


def test_future_tip_and_snapshot_excluded():
    r,t=sample();t['observed']=stamp('2026-09-01T19:31:00-04:00')
    assert link([r],[t],'t30')[0]==[]
    r['snapshot_at']='2026-09-01T19:31:00-04:00';t['observed']=stamp('2026-09-01T19:15:00-04:00')
    assert link([r],[t],'t30')[0]==[]


def test_fixed_mix_can_change_side_without_outcome_selection():
    r,t=sample();rows,_=link([r],[t],'t30')
    before=compare_fixed(rows)
    assert before['operating']['selected'][0]['side']==0
    assert before['hybrid91']['selected'][0]['side']==1
    rows[0]['y']=0
    after=compare_fixed(rows)
    assert before['hybrid91']['selected']==after['hybrid91']['selected']


def test_latest_snapshot_at_cutoff_and_one_vote_per_author():
    r,t=sample();earlier=dict(r,model=[.7,.3],snapshot_at='2026-09-01T18:00:00-04:00')
    later=dict(t,id='other',side='CLE',observed=stamp('2026-09-01T19:20:00-04:00'))
    rows,_=link([r,earlier],[later,t],'t30')
    assert rows[0]['model']==[.54,.46]
    assert rows[0]['authors']==1 and rows[0]['tipster']==[0,1]


def test_official_single_candidate_time_mismatch_rejected():
    r,t=sample();rows,_=link([r],[t],'t30')
    rows[0]['official']={'game_date':'2026-09-01T13:00:00-04:00'}
    with pytest.raises(ValueError,match='Official timing'): validate_official_timing(rows)


def test_outcomes_cannot_change_budget_and_monthly_limit():
    recs=[]
    for day in range(1,12):
        for side in range(2):
            recs.append(dict(id=f'{day}-{side}',event=f'{day}-{side}',at=f'2026-09-{day:02}T12:00:00+09:00',
                snapshot_at=f'2026-09-{day:02}T11:00:00+09:00',kickoff=f'2026-09-{day:02}T14:00:00+09:00',
                round_key='1',side=0,price=2.,probability=.6,y=0,settled_at=f'2026-09-{day:02}T15:00:00+09:00'))
    cutoff=stamp('2026-10-01T00:00:00+09:00')
    before=frozen_budget_tickets(recs,3,cutoff)
    assert before['monthly_stakes']=={'2026-09':50000}
    for r in recs: r.update(y=None,settled_at=None)
    assert frozen_budget_tickets(recs,3,cutoff)==before


def test_float_representation_is_not_new_tipster_information():
    r,t=sample();rows,_=link([r],[t],'t30')
    old=dict(rows[0],tipster=[2/3,1/3])
    new=dict(old,tipster=[2/3,1-2/3])
    assert input_changes([new],[old])['tipster_changed']==0
