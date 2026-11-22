import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import lotto_entropy_regime as m
import numpy as np

def test_exact_counts_and_initial_entropy():
 assert m.COUNTS.sum()==8145060
 assert np.isclose(m.states(np.array([0]))[0,0],1.)

def test_state_is_past_only_and_thresholds_ignore_future():
 rng=np.random.default_rng(31);a=rng.choice(64,1245,p=m.BASE);b=a.copy();b[740:]=rng.choice(64,505,p=m.BASE)
 assert np.array_equal(m.states(a)[:741],m.states(b)[:741])
 assert m.evaluate(a)[1]['thresholds']==m.evaluate(b)[1]['thresholds']

def test_probability_normalization_and_entropy_bounds():
 y=np.random.default_rng(32).choice(64,1245,p=m.BASE);f=m.states(y);_,p=m.evaluate(y)
 assert np.all(f[:,0]<=1+1e-12) and np.all(f[:,0]>=0)
 for q in p['next_probabilities']:assert np.isclose(sum(q),1) and np.all(np.array(q)[m.VALID]>0)

def test_family_p_ties():
 assert m.tail([1,2,2],2)==.75
