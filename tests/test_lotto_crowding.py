from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto_crowding import features,fit_model,predict,deviance,block_interval


def test_features_and_zero_count_deviance():
    assert features([[1,2,5,10,32,45]]).tolist()==[[4,1,3,44]]
    assert deviance(np.array([0.,2.]),np.array([1.,2.])).tolist()==pytest.approx([2,0])


def test_sales_baseline_and_training_scaling_are_fixed():
    x=np.empty((4,0)); sales=np.array([1.,2.,3.,4.]); y=2*sales
    model=fit_model(x,y,sales)
    assert predict(model,x,sales)==pytest.approx(y)
    assert predict(model,np.empty((2,0)),np.array([5.,6.]))==pytest.approx([10,12])
    assert block_interval(np.ones(30),repeats=20)==[1.,1.]
