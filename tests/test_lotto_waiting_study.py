import sys,itertools,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import lotto_waiting_study as m
import numpy as np

def test_rank_unrank_bijection_exhaustive_small_space():
 for i,t in enumerate(itertools.combinations(range(1,11),4)):
  assert m.rank_combo(t,10,4)==i and m.unrank(i,10,4)==list(t)

def test_actual_first_hit_matches_direct_stream():
 targets=[0,3,10];hit,count=m.first_hits(99,targets,space=20,batch=13)
 stream=np.random.default_rng(99).integers(0,20,count,dtype=np.int64)
 assert hit.tolist()==[int(np.flatnonzero(stream==t)[0])+1 for t in targets]

def test_selection_ignores_other_targets():
 w=np.arange(1,353).reshape(32,11);a=m.statistic(w)[1]
 w[:,1:]=w[::-1,1:]
 assert np.array_equal(a,m.statistic(w)[1])

def test_exact_joint_null_has_geometric_marginals_and_unique_arrivals():
 w=m.joint_null(np.random.default_rng(8),30000,seeds=1,targets=4,space=20)[:,0,:]
 assert np.all(np.diff(np.sort(w,axis=1),axis=1)>0)
 assert np.all(abs(w.mean(0)-20)<.5)
 assert np.all(abs((w<=10).mean(0)-(1-.95**10))<.015)

def test_uniform_ties_score_one():
 value,selected,other=m.statistic(np.ones((32,11),dtype=int))
 assert value==0 and selected.tolist()==list(range(8)) and len(other)==24
