from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import DrawRecord
from lotto_bias_null import simulate,indicator,bias_metrics,holm,native_half,compact_evaluate
from lotto_seed_expanded import MASK,N,STEP,combo_rank,unmix,ranks,uniform_controls
from lotto_seed_128 import encode_seed,ticket,evaluate


def row(n,ns):
    return DrawRecord(n,'2020-01-01',tuple(ns),next(i for i in range(1,46) if i not in ns))


def test_simulation_and_fixed_prediction_no_holdout_leakage():
    ns=simulate(np.random.default_rng(12),1238)
    assert ns.shape==(1238,6) and np.all(np.diff(ns,axis=1)>0)
    assert np.all(indicator(ns).sum(axis=1)==6)
    _,before=bias_metrics(ns)
    ns[740:]=simulate(np.random.default_rng(13),498)
    _,after=bias_metrics(ns)
    assert [r['ticket'] for r in before['predictions'].values()]==[r['ticket'] for r in after['predictions'].values()]
    assert holm([.01,.04,.03])==pytest.approx([.03,.06,.06])


def test_audit_detects_extreme_synthetic_bias():
    constant=np.tile(np.arange(6),(1238,1))
    biased,_=bias_metrics(constant)
    random,_=bias_metrics(simulate(np.random.default_rng(1),1238))
    assert biased['number_dispersion']>100*random['number_dispersion']
    assert biased['pair_dispersion']>100*random['pair_dispersion']


def test_shared_control_aggregation_equals_reference():
    ns=simulate(np.random.default_rng(2),20)+1
    draws=[row(i+1,n) for i,n in enumerate(ns)]
    odd=np.array([1,2],dtype=np.uint64); even=np.array([3,4,5],dtype=np.uint64)
    oc=uniform_controls(odd,19,1); ec=uniform_controls(even,19,2)
    a=compact_evaluate(odd,even,oc,ec,draws); b=evaluate(odd,even,oc,ec,draws)
    assert a['mean']==b['selected_mean_matches']
    assert a['selected_histogram']==b['selected_histogram']
    assert a['control_mean']==b['baseline_mean_matches']
    assert compact_evaluate([],even,np.empty((19,0)),ec,draws)['status']=='no_survivors'


def test_native_two_hit_selection_equals_scalar_enumeration(tmp_path):
    compiler=shutil.which('g++')
    if not compiler: pytest.skip('native compiler unavailable')
    exe=tmp_path/'scan.exe'
    subprocess.run([compiler,'-O2','-std=c++17','-pthread','-static','-DMIN_HITS=2',str(Path(__file__).resolve().parents[1]/'src/lotto_seed_scan.cpp'),'-o',str(exe)],check=True)
    first=row(1,[1,2,3,4,5,6]); target=combo_rank(first.numbers_sorted)
    population=(MASK-target)//N+1; stride=1000003
    import math
    while math.gcd(stride,population)!=1: stride+=1
    seeds=[(int(unmix(np.array([((j*stride+20261015)%population)*N+target],dtype=np.uint64))[0])-STEP)&MASK for j in range(100)]
    draws=[first,row(3,ticket(encode_seed(seeds[13],0),3)),row(5,[1,2,3,4,5,6])]
    selected,record=native_half(exe,draws,100)
    scalar=[s for s in seeds if sum(int(ranks([s],d.draw_no)[0])==combo_rank(d.numbers_sorted) for d in draws)>=2]
    assert sorted(map(int,selected))==sorted(scalar)
    assert seeds[13] in selected and sum(record['histogram'])==100
