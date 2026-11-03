import importlib.util,sys,copy
from pathlib import Path
import pytest
P=Path(__file__).parent;sys.path.insert(0,str(P))
import assemble,evaluate

def test_outcomes_not_loaded_when_forecast_freeze_hash_is_wrong():
 calls=[]
 def forbidden():calls.append(1);raise AssertionError("outcomes accessed before freeze")
 with pytest.raises(ValueError,match="frozen Git blob"):evaluate.evaluate("627cadca9096acae18a30f72484c342edab7acd3","0"*64,forbidden)
 assert calls==[]

def test_ensemble_averages_threeway_then_conditions():
 target=[{"fixture_id":"x","event_date":"2025-01-01"}]
 def r(h,d,a):return {"fixture_id":"x","event_date":"2025-01-01","probabilities_3way":{"home":h,"draw":d,"away":a},"probabilities_2way":{"home":h/(h+a),"away":a/(h+a)}}
 sets=[[r(.5,.1,.4)],[r(.1,.5,.4)],[r(.3,.1,.6)]];o=assemble.average(sets,target)[0]
 assert o["probabilities_3way"]["home"]==pytest.approx(.3)
 assert o["probabilities_2way"]["home"]==pytest.approx(.3/(.3+1.4/3))
 assert o["probabilities_2way"]["home"]!=pytest.approx(sum(x[0]["probabilities_2way"]["home"] for x in sets)/3)

 def bad():
  x=copy.deepcopy(o);x["probabilities_2way"]={"home":.99,"away":.01};return x
 with pytest.raises(ValueError,match="conditional"):assemble.validate([bad()],target)
 with pytest.raises(ValueError,match="cohort"):assemble.validate([],target)
