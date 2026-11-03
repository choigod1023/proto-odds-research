import copy,importlib.util,inspect,sys
from datetime import date
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-model-20261103';spec=importlib.util.spec_from_file_location('factorial_preflight_test',P/'factorial_preflight.py');f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f)

def sample():
    person={'player_id':'12','selection_basis':'official_schedule_listed_pitcher_not_independently_confirmed_pregame'}
    a={'bb':1,'date':'2026-04-01','er':2,'fixture_id':'old','hit':2,'hr':0,'kk':3,'outs':18,'r':2,'source_bf_raw':90,'source_bf_units':'unverified'}
    entry={'value':{'projected_player_id':'12','appearances':[a]},'source_fixture_ids':['old'],'latest_source_date':'2026-04-01'}
    return person,entry

def test_starter_history_scalar_and_poison():
    person,entry=sample();dates={'old':date(2026,4,1),'target':date(2026,4,2),'future':date(2026,4,3)}
    assert f.validate_starter(entry,person,'target',dates['target'],dates)==3
    for bad in ('target','future'):
        e=copy.deepcopy(entry);e['value']['appearances'][0]['fixture_id']=bad
        with pytest.raises(ValueError,match='temporal'):f.validate_starter(e,person,'target',dates['target'],dates)
    e=copy.deepcopy(entry);e['value']['appearances'][0]['date']='2026-04-02'
    with pytest.raises(ValueError,match='temporal'):f.validate_starter(e,person,'target',dates['target'],dates)

def test_invalid_pitcher_and_target_stat_fields_rejected():
    person,entry=sample();dates={'old':date(2026,4,1)}
    e=copy.deepcopy(entry);e['value']['projected_player_id']='wrong'
    with pytest.raises(ValueError,match='profile'):f.validate_starter(e,person,'target',date(2026,4,2),dates)
    e=copy.deepcopy(entry);e['value']['appearances'][0]['target_era']=0
    with pytest.raises(ValueError,match='unexpected'):f.validate_starter(e,person,'target',date(2026,4,2),dates)

def test_replacement_boundaries_and_no_scoring_in_preflight():
    a={'pitcher':1,'lineup':2,'team':3};b={'pitcher':2,'lineup':2,'team':3}
    assert f.same_except(a,b,('pitcher',))
    b['team']=4;assert not f.same_except(a,b,('pitcher',))
    src=inspect.getsource(f.main);assert 'test-labels' not in src and 'model.predict' not in src and 'model.fit' not in src

def test_source_id_date_team_fallback_and_wrong_fixture():
    row={'season':2026,'event_date':'2026-08-20','home_id':'LG','away_id':'KT'}
    assert f.fixture_matches('score:2026-08-20:LG:KT','20260820KTLG0',row)
    assert not f.fixture_matches('score:2026-08-20:KT:LG','20260820KTLG0',row)
    assert f.fixture_matches('20260820KTLG02026','20260820KTLG0',row)
