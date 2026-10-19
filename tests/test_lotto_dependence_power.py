import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import itertools
import math
import random
import numpy as np
import pytest
import lotto_dependence_power as p

@pytest.fixture(scope='module')
def native():
    return p.Native()

def encode(rows):return np.array([sum(1<<v for v in r) for r in rows],dtype=np.uint64)
def decode(values):return [{v for v in range(45) if int(x)&(1<<v)} for x in values]

def test_all_effects_preserve_single_marginals_and_triple_preserves_pairs():
    for kind in ('pair','pure_triple'):
        k=2 if kind=='pair' else 3
        j=np.arange(k+1)
        for strength in [1]+p.LEVELS[kind]:
            base,q=p.category_probabilities(kind,strength)
            assert np.isclose(q.sum(),1) and q.min()>=0
            assert np.isclose(j@q,j@base)
            assert np.isclose(q[-1]/base[-1],strength)
            if k==3:assert np.isclose((j*(j-1))@q,(j*(j-1))@base)

def test_generator_is_reproducible_and_valid():
    for kind,level in [('null',1),('pair',2),('pure_triple',3),('diffuse',.1)]:
        rows,target=p.generate(kind,level,123,n=100)
        assert (rows,target)==p.generate(kind,level,123,n=100)
        assert all(len(r)==6 and min(r)>=0 and max(r)<45 for r in rows)

def test_native_zero_steps_counts_equal_python(native):
    rows,_=p.generate('null',1,77,n=200)
    values=encode(rows);output=np.empty((1,15180),dtype=np.uint16)
    assert native.lib.spoke_counts(values,200,1,0,200,1,output)==0
    assert np.array_equal(output[0],p.old.counts(rows))

def test_native_preserves_block_margins(native):
    rows,_=p.generate('pure_triple',5,123,n=1000)
    result=decode(native.walk(encode(rows),20000,19))
    assert np.array_equal(p.old.margins(rows,200),p.old.margins(result,200))
    assert all(len(r)==6 for r in result)

def test_native_trade_matches_uniform_partition_law(native):
    rows=[set(range(6)),{0,1,2,3,6,7}];x=encode(rows)
    tally={tuple(t):0 for t in itertools.combinations(range(4,8),2)}
    for seed in range(6000):
        after=decode(native.walk(x,1,seed,block=2))
        key=tuple(sorted(after[0]-{0,1,2,3}));tally[key]+=1
    assert all(abs(v-1000)<120 for v in tally.values())

def test_native_rejects_invalid_row(native):
    x=np.array([1,3],dtype=np.uint64);out=np.empty_like(x)
    assert native.lib.endpoint(x,2,1,2,10,out)==2

def test_intervals_are_nonzero_at_boundaries():
    lo,hi=p.exact_interval(0,30)
    assert lo==0 and .11<hi<.12
    lo,hi=p.exact_interval(30,30)
    assert .88<lo<.89 and hi==1

def test_fixed_grid_size_and_balanced_null():
    jobs=p.jobs()
    assert len(jobs)==510 and sum(k=='null' for k,_,_ in jobs)==60
