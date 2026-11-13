import importlib.util
from pathlib import Path
import numpy as np

spec=importlib.util.spec_from_file_location('study',Path(__file__).parents[1]/'scripts/lotto_nonlinear.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_features_are_invariant_to_draw_order():
    d=m.uniform(np.random.default_rng(1),100)
    assert np.array_equal(m.features(d),m.features(d[:,::-1]))
    assert (np.diff(np.sort(d,axis=1),axis=1)>0).all()

def test_xor_injection_is_exact_and_keeps_valid_draws():
    d=m.injected(np.random.default_rng(3),1.)
    f=m.features(d)
    assert np.array_equal(f[2:,0],f[1:-1,0]^f[:-2,0])
    assert (np.diff(np.sort(d,axis=1),axis=1)>0).all()

def test_future_changes_do_not_change_fit():
    rng=np.random.default_rng(4);f=m.features(m.uniform(rng,m.N))
    _,before=m.scores(f,True);f[m.TRAIN:]=m.features(m.uniform(rng,m.N-m.TRAIN))
    _,after=m.scores(f,True)
    for a,b in zip(before,after):
        assert a['base']==b['base'] and a['probabilities']==b['probabilities']
        assert np.allclose(np.array(a['probabilities']).sum(1),1)

def test_contexts_only_use_previous_rounds():
    rng=np.random.default_rng(5);f=m.features(m.uniform(rng,m.N));g=f.copy();g[800:]=0
    for j in range(4):
        for a,b in zip(m.contexts(f,j),m.contexts(g,j)):
            assert np.array_equal(a[:791],b[:791])

def test_score_matches_manual_loop_and_split_sum():
    f=m.features(m.uniform(np.random.default_rng(6),m.N));score,params=m.scores(f,True)
    for index in range(24):
        j,c=divmod(index,6);p=params[index];ctx=m.contexts(f,j)[c]
        manual=sum(np.log(p['probabilities'][ctx[t-m.WARM]][f[t,j]]/p['base'][f[t,j]]) for t in range(m.TRAIN,m.N))
        assert np.isclose(score[index],manual)
        assert np.isclose(score[index],p['early_gain']+p['late_gain'])

def test_family_tail_controls_selection_and_ties():
    ref=np.array([[0,3],[2,1],[4,2]])
    assert m.tail(ref.max(1),3)==.75
    assert m.tail(ref.max(1),3)>=m.tail(ref[:,0],3)
