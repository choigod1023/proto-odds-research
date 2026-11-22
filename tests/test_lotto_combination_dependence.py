import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import itertools
import random
from fractions import Fraction
import numpy as np
import lotto_combination_dependence as m

def test_counts_against_set_definition():
    rows=[set(range(6)),set(range(3,9)),{0,3,10,22,31,44}]
    actual=m.counts(rows)
    expected=[sum(set(f)<=r for r in rows) for f in m.FEATURES]
    assert actual.tolist()==expected
    assert int(actual[:990].sum())==45 and int(actual[990:].sum())==60

def test_walk_preserves_exact_block_margins_and_row_sizes():
    rng=random.Random(45)
    rows=[set(rng.sample(range(45),6)) for _ in range(20)]
    before=m.margins(rows,10)
    m.walk(rows,1000,10,rng)
    assert np.array_equal(before,m.margins(rows,10))
    assert all(len(row)==6 for row in rows)

def test_trade_handles_identical_and_disjoint_rows():
    rows=[set(range(6)),set(range(6)),set(range(6,12))]
    before=m.margins(rows,3)
    rng=random.Random(9)
    m.trade(rows,0,1,rng)
    assert rows[0]==rows[1]==set(range(6))
    m.trade(rows,0,2,rng)
    assert np.array_equal(before,m.margins(rows,3))

def test_finite_curveball_transition_detailed_balance():
    # Exhaust every fixed-margin 4x4 matrix with two ones per row/column.
    choices=list(itertools.combinations(range(4),2))
    states=[tuple(tuple(r) for r in rows) for rows in itertools.product(choices,repeat=4)
            if all(sum(v in r for r in rows)==2 for v in range(4))]
    transitions={}
    for state in states:
        dist={}
        for i,j in itertools.combinations(range(4),2):
            a,b=set(state[i]),set(state[j]);common=a&b;pool=sorted(a^b)
            splits=list(itertools.combinations(pool,len(a-common)))
            for take in splits:
                changed=list(state)
                changed[i]=tuple(sorted(common|set(take)))
                changed[j]=tuple(sorted(common|(set(pool)-set(take))))
                key=tuple(changed)
                dist[key]=dist.get(key,Fraction(0))+Fraction(1,6*len(splits))
        assert sum(dist.values())==1
        transitions[state]=dist
    for a,dist in transitions.items():
        for b,p in dist.items():assert transitions[b].get(a,0)==p

def test_sampler_reproducible_and_invariants():
    rows=[set(range(i,i+6)) for i in range(6)]
    a,da=m.sample(rows,replicates=3,steps=15,block=3,seed=100)
    b,db=m.sample(rows,replicates=3,steps=15,block=3,seed=100)
    assert np.array_equal(a,b) and da==db
    assert np.array_equal(a[0],m.counts(rows))

def test_symmetric_standardization_and_ties():
    matrix=np.ones((10,15180),dtype=np.int16)
    result,selected=m.analyze(matrix)
    assert selected==[]
    assert all(v['raw_p']==1 for v in result['global_tests'].values())

def test_local_family_correction_detects_strong_outlier():
    matrix=np.zeros((100,15180),dtype=np.int16)
    matrix[0,0]=100
    result,selected=m.analyze(matrix)
    assert selected==[0]
    assert result['selected'][0]['family_adjusted_p']==.03
    permuted=matrix[np.r_[0,np.arange(99,0,-1)]]
    assert m.analyze(permuted)==(result,selected)
