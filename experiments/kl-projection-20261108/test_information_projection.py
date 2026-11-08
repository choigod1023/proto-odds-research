import importlib.util
from pathlib import Path
import numpy as np
spec=importlib.util.spec_from_file_location('kl',Path(__file__).with_name('run.py')); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_coherent_distribution_and_conflicting_targets():
 q=m.poisson(1.5,1.2); offers=[(m.masks('승무패',None),np.array([.8,.1,.1])),(m.masks('핸디캡',-1),np.array([.1,.1,.8])),(m.masks('언더오버',2.5),np.array([.8,.2]))]
 p=m.project(q,offers)
 assert np.isclose(p.sum(),1) and np.all(p>=0)
 assert all(np.isclose((mask@p).sum(),1) for mask,_ in offers)
 assert np.linalg.norm(offers[0][0]@p-offers[0][1])>1e-5
def test_push_and_three_way():
 assert m.choose_outcome('언더오버',2,1,1) is None
 assert m.choose_outcome('핸디캡',-1,2,1)==1
 assert m.masks('언더오버',2)[:,(m.H==1)&(m.A==1)].sum()==0
 assert m.choose_outcome('승무패',None,1,1)==1
