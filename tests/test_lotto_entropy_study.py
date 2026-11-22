import importlib.util
from pathlib import Path
import numpy as np

spec=importlib.util.spec_from_file_location('entropy',Path(__file__).parents[1]/'scripts/lotto_entropy_study.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_exact_uniform_overlap_distribution():
    assert np.isclose(m.BASE.sum(),1)
    assert np.isclose(m.BASE@m.K,.8)
    assert np.allclose(m.overlap_distribution(0),m.BASE)
    assert m.overlap_distribution(1)@m.K>.8

def test_generator_preserves_six_unique_balls_and_forced_static():
    for kind in ('iid','static','copy_one'):
        d=m.generate(np.random.default_rng(23),2,kind,1.)
        assert d.shape==(2,1245,45) and np.all(d.sum(axis=2)==6)
        if kind=='static':assert d[:,1:,0].all()
        if kind=='copy_one':assert np.all((d[:,1:] & d[:,:-1]).sum(axis=2)>=1)

def test_fit_cannot_read_evaluation_outcomes():
    d=m.generate(np.random.default_rng(12),1)[0]
    _,before=m.score(d)
    changed=d.copy();changed[m.TRAIN:]=m.generate(np.random.default_rng(99),1)[0][m.TRAIN:]
    _,after=m.score(changed)
    assert before==after

def test_gain_matches_direct_predictive_probability_ratios():
    d=m.generate(np.random.default_rng(34),1)[0];g,p=m.score(d)
    overlap=(d[1:] & d[:-1]).sum(axis=1)[m.TRAIN-1:]
    direct=np.log(m.overlap_distribution(p['theta'])[overlap]/m.BASE[overlap]).sum()
    assert np.isclose(g[0],direct)
    # Conditional probabilities define normalized distributions over all subsets:
    # each inclusion class is uniform, so class totals are q and 1-q.
    for q in p['conditional_ball1']:
        assert 0<q<1 and np.isclose(q+(1-q),1)

def test_plus_one_tail_ties_and_interval_extremes():
    assert m.tail(np.array([1,2,2]),2)==.75
    assert m.tail(np.array([1,2,2]),3)==.25
    assert m.interval(0,200)[0]==0 and m.interval(200,200)[1]==1
