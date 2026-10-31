"""Regression, finite-difference and temporal-leakage checks, no ROI guarantees."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import numpy as np

HERE=Path(__file__).resolve().parents[1]/'experiments/contextual-stacking-20261031'
# Isolate experiment imports from production modules and other experiment tests.
def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module

_old_common=sys.modules.get('common');_old_model=sys.modules.get('model')
try:
    common=load('common','common.py');model=load('model','model.py');runner=load('contextual_stacking_run_test','run.py')
    verifier=load('contextual_stacking_verify_test','verify.py')
finally:
    for name,old in (('common',_old_common),('model',_old_model)):
        if old is None:sys.modules.pop(name,None)
        else:sys.modules[name]=old


class ContextualStackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=common.read_gz(HERE/'inputs/features.jsonl.gz')
        cls.labels={r['id']:r for r in common.read_gz(HERE/'inputs/labels.jsonl.gz')}

    def test_analytic_gradient_matches_finite_difference(self):
        rng=np.random.default_rng(71);x=rng.normal(size=(12,4));p=rng.uniform(.01,.99,size=(12,4))
        theta=rng.normal(scale=.3,size=12);penalty=np.array([1.,2.,2.,1.])
        value,g=model.objective(theta,x,p,.01,penalty)
        numerical=[]
        for i in range(len(theta)):
            step=np.zeros(len(theta));step[i]=1e-6
            numerical.append((model.objective(theta+step,x,p,.01,penalty)[0]-model.objective(theta-step,x,p,.01,penalty)[0])/2e-6)
        self.assertTrue(np.isfinite(value));np.testing.assert_allclose(g,numerical,atol=1e-8,rtol=1e-6)

    def test_eval_label_flip_does_not_change_any_fits_or_forecasts(self):
        original,models,test=model.study(self.rows,self.labels)
        poison=copy.deepcopy(self.labels)
        for row in test:poison[row['id']]['outcome']=(poison[row['id']]['outcome']+1)%len(row['sides'])
        altered,other,again=model.study(self.rows,poison)
        self.assertEqual(common.canonical(original),common.canonical(altered))
        for name in models:
            self.assertEqual(common.canonical(models[name].predict(test)),common.canonical(other[name].predict(again)))
        self.assertEqual((original['train_n'],original['validation_n'],original['refit_n'],original['test_n']),(261,50,322,122))

    def test_late_label_fit_is_rejected(self):
        row=self.rows[0];labels=copy.deepcopy(self.labels)
        labels[row['id']]['available_at_proxy']='2026-08-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'unavailable'):
            model.fit('global:0.01',[row],labels,model.JUNE)

    def test_simplex_prediction_and_deterministic_source_features(self):
        row=copy.deepcopy(self.rows[0]);x,p=model.matrices([row],'contextual')
        fake=copy.deepcopy(row);fake.update(outcome=999,final_score=[999,0],profit=123)
        self.assertEqual(model.covariates(row),model.covariates(fake))
        stack=model.Stack('contextual:0.1',np.zeros((x.shape[1],3)).tolist())
        pred,w=stack.predict([row]);self.assertAlmostEqual(sum(pred[0]),1.,places=12)
        self.assertAlmostEqual(sum(w[0]),1.,places=12);self.assertTrue(all(t>=0 for t in w[0]))
        np.testing.assert_allclose(pred[0],p.mean(axis=1)[0,:len(row['sides'])])

    def test_negative_component_and_unknown_league_rejected(self):
        row=copy.deepcopy(self.rows[0]);row['components']['market'][0]=-1
        with self.assertRaises(ValueError):model.matrices([row],'global')
        row=copy.deepcopy(self.rows[0]);row['league']='unknown'
        with self.assertRaises(ValueError):model.matrices([row],'contextual')

    def test_tied_vote_score_is_not_arbitrarily_treated_as_one_direction(self):
        row=copy.deepcopy(self.rows[0]);n=len(row['sides'])
        row['raw_vote_score']=[1/n]*n
        with self.assertRaisesRegex(ValueError,'ambiguous tipster'):
            model.covariates(row)

    def test_underdog_not_defined_by_220_threshold(self):
        row=copy.deepcopy(self.rows[0]);row['sides']=['home','away'];row['odds']=[1.4,1.9]
        row['components']['market']=[.6,.4]
        result=runner.settle(row,[.4,.6],{'outcome':1},'synthetic')
        self.assertEqual(result['group'],'underdog');self.assertEqual(result['profit'],'0.9')
        row['components']['market']=[.5,.5]
        self.assertEqual(runner.settle(row,[.4,.6],{'outcome':1},'synthetic')['group'],'top_tie')

    def test_unanimous_unique_errors_cannot_be_repaired_by_convex_reweighting(self):
        p=np.array([[.7,.2,.1],[.5,.3,.2],[.6,.1,.3],[.8,.15,.05]])
        rng=np.random.default_rng(2)
        for w in rng.dirichlet(np.ones(4),size=100):self.assertEqual(int(np.argmax(w@p)),0)
        # Different wrong component argmaxes can jointly recover a third class.
        nonunanimous=np.array([[.45,.4,.15],[.15,.4,.45]])
        self.assertEqual(int(np.argmax(nonunanimous.mean(axis=0))),1)

    def test_feature_files_have_no_outcomes_and_correct_sources(self):
        self.assertEqual(len(self.rows),445)
        for row in self.rows:
            self.assertFalse({'outcome','hit','profit','logloss'}&set(row))
            self.assertFalse(row['external_receipt_verified']);self.assertFalse(row['price_certified'])
        record=json.loads((HERE/'inputs/provenance.json').read_text(encoding='utf8'))
        self.assertEqual(common.sha(HERE/'inputs/features.jsonl.gz'),record['features_sha256'])

    def test_independent_decimal_settlements_and_grouped_summaries(self):
        result=verifier.verify()
        self.assertEqual(result['independent_decimal_settlements_checked'],1220)
        self.assertEqual(result['mismatches'],0)


if __name__=='__main__':unittest.main()
