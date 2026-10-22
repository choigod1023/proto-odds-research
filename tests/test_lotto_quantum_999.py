from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto_quantum_999 import tail_summary


def test_tail_ties_and_plus_one():
    r=tail_summary(2,[1,2,3])
    assert r["exceedances"]==2
    assert r["p_upper_plus_one"]==.75
    assert r["tail_probability_ci95"][0]<2/3<r["tail_probability_ci95"][1]


def test_tail_extremes():
    a=tail_summary(2,[0]*999)
    assert a["p_upper_plus_one"]==.001 and a["tail_probability_ci95"][0]==0
    b=tail_summary(0,[0]*999)
    assert b["p_upper_plus_one"]==1 and b["tail_probability_ci95"][1]==1
