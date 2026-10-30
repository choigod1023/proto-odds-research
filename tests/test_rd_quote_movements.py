from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import rd_quote_movements as m


def case():
    return dict(cohort='saved_pregame',event_id='x',league='KBO',home='A',away='B',kickoff='2026-10-30T10:00:00+09:00',
                decision_at='2026-10-30T09:30:00+09:00',issue=[2026,100],game_no='1',sides=['home','away'],odds=[1.8,2.])


def frame(hour=8,prices=(1.8,2.)):
    return dict(snapshot_id=f's{hour}',event_id='x',league='KBO',home='A',away='B',kickoff='2026-10-30T10:00:00+09:00',
                as_of=f'2026-10-30T{hour:02}:05:00+09:00',captured_at=f'2026-10-30T{hour:02}:06:00+09:00',
                quote_observed_at=f'2026-10-30T{hour:02}:00:00+09:00',
                choices=[dict(market='승패',selection=s,issue_year=2026,round=100,game_no='1',odds=v)
                         for s,v in zip(('홈','원정'),prices)])


def test_real_price_changes_use_only_predecision_complete_product():
    rows,audit=m.extract([case()],[frame(8,(2.,1.8)),frame(9)],'a'*64)
    f=rows[0]['features']
    assert f['home_implied_change_pp']>0 and f['changed_quote_count']==1 and f['history_hours']==1
    assert audit['events_with_actual_price_change']==1


def test_legacy_missing_fixture_is_audited_not_guessed_or_crashed():
    bad=frame();bad['home']=None
    rows,audit=m.extract([case()],[bad,frame(8),frame(9)],'a'*64)
    assert len(rows)==1 and audit['source_frame_missing_fixture']==1


def test_repeated_model_revisions_with_same_quote_are_not_two_observations():
    a,b=frame(),frame();b.update(snapshot_id='newmodel',captured_at='2026-10-30T09:15:00+09:00')
    rows,audit=m.extract([case()],[a,b],'a'*64)
    assert rows==[] and audit['fewer_than_two_distinct_quote_times']==1


def test_unchanged_prices_at_distinct_quote_times_are_zero_change_not_missing():
    rows,audit=m.extract([case()],[frame(8),frame(9)],'a'*64)
    assert rows[0]['features']['home_implied_change_pp']==0
    assert rows[0]['features']['changed_quote_count']==0


def test_wrong_issue_price_stale_or_future_quotes_fail_closed():
    c=case();future=frame(10)
    assert m.extract([c],[frame(8),future],'a'*64)[0]==[]
    wrong=frame(9);wrong['choices'][0]['round']=101
    assert m.extract([c],[frame(8),wrong],'a'*64)[0]==[]
    assert m.extract([c],[frame(5),frame(6)],'a'*64)[0]==[]
    assert m.extract([c],[frame(8),frame(9,(1.9,2.1))],'a'*64)[0]==[]


def test_same_quote_time_conflict_does_not_cherry_pick_a_price():
    rows,audit=m.extract([case()],[frame(8),frame(9),frame(9,(2.,2.))],'a'*64)
    assert rows==[] and audit['conflicting_same_time_quote_payload']==1


def test_source_projection_drops_all_target_labels_and_scores():
    raw=frame();raw.update(outcome='home',result='win')
    for choice in raw['choices']:choice.update(hit=True,refund=False,scores={'final':1.})
    p=m.project(raw)
    assert 'outcome'not in p and 'result'not in p
    assert all(not {'hit','refund','scores'}&set(c)for c in p['choices'])
    a=m.extract([case()],[frame(8),frame(9)],'a'*64)
    raw['choices'][0]['hit']=False
    assert m.project(raw)==p and a==m.extract([case()],[frame(8),frame(9)],'a'*64)
