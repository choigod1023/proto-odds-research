import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nba_score_components_research as m

def test_counts_score_expectation_and_tie():
    # Equal sides imply an exactly symmetric win probability even with integer ties.
    a=np.array([100,48,25,35,13,22,17.])
    history=[(a,a)]*10
    pr=np.array([.48,25/48,.35,13/35,.22,17/22])
    assert abs(m.probability([history,history],pr)-.5)<1e-10
    expected=100*(2*.48*(25/48)+3*.35*(13/35)+.22*(17/22))
    assert abs(expected-106)<1e-12

def test_shrinkage_and_sample_size():
    pr=np.array([.5,.5,.35,.36,.22,.77])
    assert np.allclose(m.rates(np.zeros(7),pr),pr)
    assert np.all(np.isfinite(m.rates(np.zeros(7),pr)))

def test_missing_summary_is_not_replaced(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CACHE',tmp_path)
    (tmp_path/'summary').mkdir()
    assert m.box({'id':'404','teams':['A','B'],'stats':[{},{}]}) is None

def test_summary_identity_mismatch_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CACHE',tmp_path)
    (tmp_path/'summary').mkdir()
    (tmp_path/'summary'/'x.json').write_text('{}')
    assert m.box({'id':'x','summary_invalid':True}) is None

def test_home_tie_rule_and_daily_drawdown():
    rows=[{'day':'2025-01-01','home_win':1,'odds':[2,2],'components':.5}, {'day':'2025-01-02','home_win':0,'odds':[2,2],'components':.5}]
    v=m.calc(rows,'components')
    assert v['wins']==1 and v['daily_mdd']==1


def test_causal_window_ignores_future_and_never_backfills_missing():
    from datetime import date, timedelta
    start=date(2025,1,1)
    hist=[{'id':str(i),'day':(start+timedelta(days=i)).isoformat()} for i in range(20)]
    before=m.latest_ids(hist,'2025-01-20')
    hist.append({'id':'future','day':'2025-01-21'})
    assert m.latest_ids(hist,'2025-01-20')==before==[str(i) for i in range(6,16)]
    # Selection retains a missing record; the caller must reject its missing box.
    hist[10]['missing']=True
    assert m.latest_ids(hist,'2025-01-20')==before

def test_gzip_is_byte_deterministic():
    assert m.deterministic_gzip(b'NBA\n')==m.deterministic_gzip(b'NBA\n')

def test_bad_score_box_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CACHE',tmp_path)
    (tmp_path/'summary').mkdir(); (tmp_path/'summary'/'x.json').write_text('{}')
    stats=[{'name':'threePointFieldGoalsMade-threePointFieldGoalsAttempted','displayValue':'1-2'},{'name':'freeThrowsMade-freeThrowsAttempted','displayValue':'2-2'}]
    monkeypatch.setattr(m.old,'verified_json',lambda p:{'boxscore':{'teams':[{'team':{'abbreviation':t},'statistics':stats} for t in ('A','B')]}})
    s={'fga':5,'fgm':3,'orb':1,'tov':1}
    assert m.box({'id':'x','teams':['A','B'],'scores':[99,99],'stats':[s,s]}) is None

def test_conflicting_source_id_fails_closed(tmp_path):
    import json,hashlib,pytest
    folder=tmp_path/'scoreboard';folder.mkdir()
    for i in range(2):
        path=folder/f'{i}.json'; raw=json.dumps({'events':[{'id':'same','value':i}]}).encode()
        path.write_bytes(raw);path.with_suffix('.receipt.json').write_text(json.dumps({'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
    with pytest.raises(ValueError,match='conflicting scoreboard'):
        m.old.read_events(tmp_path)
