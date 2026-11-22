import importlib.util,sys
from pathlib import Path
from decimal import Decimal
import pytest
spec=importlib.util.spec_from_file_location("ranking_compare",Path(__file__).with_name("compare.py"));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

def test_refund_denominator_and_daily_drawdown():
 rows=[];fs={};labels={}
 for fid,day,home,away in [("a","2026-01-01",2,1),("b","2026-01-02",1,2),("c","2026-01-03",1,1)]:
  truth="home" if home>away else "away" if home<away else "draw";rows.append({"fixture_id":fid,"date":day,"winner":truth,"price_source":{"prices":{"home":1.8,"away":2.0}}});fs[fid]={"q2":{"home":.6,"away":.4}};labels[fid]={"status":"final","home_score":home,"away_score":away}
 m,rs=c.settle(rows,fs,labels)
 assert m["n"]==3 and m["refunds"]==1 and m["hits"]==1 and m["decisive"]==2
 assert Decimal(m["net_units"])==Decimal("-0.2") and m["roi"]==pytest.approx(-.2/3)
 assert Decimal(m["max_daily_close_drawdown_units"])==1 and m["monthly_units"]=={"2026-01":"-0.2"}

def test_duplicate_and_unequal_pairs_rejected():
 with pytest.raises(ValueError,match="duplicate"):c.unique([{"fixture_id":"x"},{"fixture_id":"x"}])
 with pytest.raises(ValueError,match="paired fixture"):c.paired([{"fixture_id":"x"}],[{"fixture_id":"y"}])

def test_invalid_probabilities_and_nonfinal_labels_rejected():
 with pytest.raises(ValueError,match="probabilities"):c.forecast({"probabilities_2way":{"home":.7,"away":.7}})
 with pytest.raises(ValueError,match="final score"):c.winner({"status":"pending","home_score":0,"away_score":0})

def test_existing_independent_result_reference():
 out=c.build();old=c.read("54dbcfcdd9ffc3ca0ad473cf9d586a38398cde4e",c.NB+"artifacts/results.json")
 for selector,models in old["results"].items():
  for name,value in models["outcome"].items():
   key="matched."+selector+"."+name;r=out["full_709_outcomes"][key]
   assert r["hits"]==value["threeway"]["hits"] and r["accuracy"]==pytest.approx(value["threeway"]["accuracy"])
  for tier,value in models["price_cohorts"].items():
   for name,m in value["models"].items():
    r=out["price_cohorts"][tier]["metrics"]["matched."+selector+"."+name]
    assert r["n"]==m["bets"] and r["refunds"]==m["voids"] and r["hits"]==m["hits"]
    assert Decimal(r["net_units"])==Decimal(m["net_units"]) and r["roi"]==pytest.approx(m["roi_including_refunds"])
    assert r["monthly_units"]==m["monthly_units"] and Decimal(r["max_daily_close_drawdown_units"])==Decimal(m["max_daily_close_drawdown_units"])


def test_independently_reported_tipster_intersections():
 sys.path.insert(0,str(Path(__file__).parent))
 import compare_tipsters
 out=compare_tipsters.main();a=out["tipster_common"];b=out["e0_common"]
 assert a["n"]==197 and b["n"]==196 and len(a["metrics"])==14
 for name,hits,net in [("M0_actual",119,"-11.72"),("market90_tipster10",116,"-18.43"),("tipster_vote",110,"-20.33"),("state.frozen.outcome-static",107,"-23.94")]:
  m=a["metrics"][name];assert m["hits"]==hits and Decimal(m["net_units"])==Decimal(net) and m["refunds"]==0
 assert b["metrics"]["M0_actual"]["hits"]==b["metrics"]["e0"]["hits"]==118
 assert Decimal(b["metrics"]["M0_actual"]["net_units"])==Decimal("-12.50") and Decimal(b["metrics"]["e0"]["net_units"])==Decimal("-14.17")
 assert {r["fixture_id"] for r in out["excluded_tipster"]}=={r["fixture_id"] for r in out["excluded_e0"]}=={"20260702KTHH02026","20260709LGSS02026"}
