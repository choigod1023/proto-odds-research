import sys
import json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pytest
import lotto_split_screen as s

@pytest.fixture(scope='module')
def native():return s.Native()

def test_ragged_blocks_preserve_exact_margins(native):
    rows,_=s.prior.generate('pair',2,91,n=245)
    original=np.array([sum(1<<v for v in row) for row in rows],dtype=np.uint64)
    ends=np.array([100,245],dtype=np.int32);out=np.empty_like(original)
    assert native.lib.split_hub(original,245,ends,2,4900,11,out)==0
    decoded=[{v for v in range(45) if int(x)&(1<<v)} for x in out]
    for a,b in ((0,100),(100,245)):
        assert np.array_equal(s.prior.old.margins(rows[a:b],b-a),s.prior.old.margins(decoded[a:b],b-a))

def test_zero_step_counts_and_invalid_partition(native):
    rows,_=s.prior.generate('null',1,7,n=10)
    x=np.array([sum(1<<v for v in r) for r in rows],dtype=np.uint64)
    ends=np.array([4,10],dtype=np.int32);out=np.empty((1,15180),dtype=np.uint16)
    assert native.lib.split_spokes(x,10,ends,2,1,0,7,out)==0
    assert np.array_equal(out[0],s.prior.old.counts(rows))
    bad=np.array([1,10],dtype=np.int32);hub=np.empty_like(x)
    assert native.lib.split_hub(x,10,bad,2,1,7,hub)==2

def test_screen_balances_families_and_freezes_negative_sign():
    x=np.ones((10,15180),dtype=np.uint16)*10
    x[0,22]=0;x[0,1010]=20
    c=s.screen(x)
    assert [v['index'] for v in c]==[22,1010]
    assert [v['direction'] for v in c]==[-1,1]

def test_validation_sign_bonferroni_and_ties():
    x=np.zeros((100,15180),dtype=np.uint16);x[0,1]=3
    c=[{'index':1,'direction':1},{'index':1000,'direction':-1}]
    out=s.confirm(x,c)
    assert out[0]['raw_p']==.01 and out[0]['adjusted_p']==.02 and out[0]['confirmed']
    assert out[1]['raw_p']==1 and not out[1]['confirmed']
    assert c==[{'index':1,'direction':1},{'index':1000,'direction':-1}]

def test_full_null_ties_never_significant():
    out=s.full_test(np.zeros((10,15180),dtype=np.uint16),12)
    assert out=={'any_p':1.,'any':False,'target_p':1.,'target':False}

def test_fixed_gate_requires_improvement_not_just_good_power():
    rows=[]
    for i,(kind,strength,rep) in enumerate(s.jobs()):
        hit=kind!='null'
        rows.append({'kind':kind,'strength':strength,'split_any':hit,'split_target':hit,'full_target':hit})
    g=s.gate(rows)
    assert not g['passed'] and g['paired_one_sided_p']==1 and g['weak_gain']==0
    for r in rows:
        if (r['kind'],r['strength']) in s.WEAK:r['full_target']=False
    assert s.gate(rows)['passed']
    assert json.loads(json.dumps(s.gate(rows)))['passed']

def test_partition_sampler_reproducible(native):
    rows,_=s.prior.generate('null',1,123,n=20)
    a=native.sample(rows,[8,20],9,41)
    assert np.array_equal(a,native.sample(rows,[8,20],9,41))

def test_grid_and_no_actual_data_dependency():
    assert len(s.jobs())==280
    assert sum(k=='null' for k,_,_ in s.jobs())==100
    assert sum((k,v) in s.WEAK for k,v,_ in s.jobs())==120
