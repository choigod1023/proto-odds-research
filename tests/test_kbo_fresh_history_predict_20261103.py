import copy,importlib.util,inspect,sys
from datetime import date
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-model-20261103';spec=importlib.util.spec_from_file_location('fresh_prediction_test',P/'fresh_history_predict.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
def entry():return {'availability_basis':'prior_calendar_day_assumed_completed_not_received','received_at':None,'completed_at':None,'latest_source_date':'2026-07-25','source_fixture_ids':['old'],'value':[{'player_id':str(i),'history':[{'fixture_id':'old','date':'2026-07-25','ab':1,'hit':0,'bb':0,'hr':0}]} for i in range(9)]}
def test_batter_partial_unknown_not_zero_and_zeros_are_observed():
 e=entry();dates={'old':date(2026,7,25),'target':date(2026,7,26),'future':date(2026,7,27)};assert m.validate_entry('home_lineup_strength',e,'target',dates['target'],dates)==0
 for bad in (None,'missing'):
  z=copy.deepcopy(e);z['value'][0]['history'][0]['bb']=bad
  with pytest.raises(ValueError,match='incomplete'):m.validate_entry('home_lineup_strength',z,'target',dates['target'],dates)
 z=copy.deepcopy(e);z['value'][0]['history'][0]['bb_hbp']=1
 with pytest.raises(ValueError,match='incomplete'):m.validate_entry('home_lineup_strength',z,'target',dates['target'],dates)
def test_same_target_future_history_forbidden():
 e=entry();dates={'old':date(2026,7,25),'target':date(2026,7,26),'future':date(2026,7,27)}
 for fid in ('target','future'):
  z=copy.deepcopy(e);z['value'][0]['history'][0]['fixture_id']=fid
  with pytest.raises(ValueError,match='temporal'):m.validate_entry('home_lineup_strength',z,'target',dates['target'],dates)
 z=copy.deepcopy(e);z['value'][0]['history'][0]['date']='2026-07-26'
 with pytest.raises(ValueError,match='temporal'):m.validate_entry('home_lineup_strength',z,'target',dates['target'],dates)
def test_no_fitting_or_outcomes():
 text=inspect.getsource(m.build);assert 'model.fit=m.forbid_training' in text and 'model.minimize=m.forbid_training' in text
 assert 'test-labels' not in text and 'test-labels' not in inspect.getsource(m.load)
