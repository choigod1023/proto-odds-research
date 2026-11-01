import copy
import json
import unittest
import numpy as np
from scipy.optimize._numdiff import approx_derivative
from src.research_moe_two_expert import fit,predict,features,objective


def examples():
    rng=np.random.default_rng(74);rows=[]
    for i in range(150):
        q=rng.dirichlet([2,2,2]);d=rng.dirichlet([3,2,1])
        rows.append(dict(id=str(i),day='2024-06-01',market_p=q.tolist(),dc_p=d.tolist(),
                         unknown_dc_team=i%7==0,y=int(rng.choice(3,p=.7*q+.3*d))))
    return rows


class Gate(unittest.TestCase):
    def test_actual_gradient(self):
        rows=examples();raw=np.array([features(r) for r in rows]);x=np.c_[np.ones(len(rows)),raw]
        q=np.array([r['market_p'] for r in rows]);d=np.array([r['dc_p'] for r in rows]);y=np.array([r['y'] for r in rows])
        beta=np.array([.2,-.4,.6,-.2,.8]);args=(x,q,d,y)
        grad=objective(beta,*args)[1]
        numeric=approx_derivative(lambda b:objective(b,*args)[0],beta,method='3-point').ravel()
        np.testing.assert_allclose(grad,numeric,atol=2e-7,rtol=1e-6)

    def test_roundtrip_and_prediction_outcome_invariance(self):
        rows=examples();state=fit(rows,'2024-07-01');row=copy.deepcopy(rows[0])
        p,detail=predict(json.loads(json.dumps(state)),row)
        row.update(y=100,hg=10000,ag=-1,prices=['999','1.01','2'])
        self.assertEqual(p,predict(state,row)[0]);self.assertAlmostEqual(sum(p),1)
        self.assertGreater(detail['market_weight'],0);self.assertLess(detail['market_weight'],1)
        self.assertFalse(state['tipster_expert'])

    def test_fit_rejects_future_and_duplicates(self):
        rows=examples();rows[0]['day']='2024-07-01'
        with self.assertRaisesRegex(ValueError,'future'):fit(rows,'2024-07-01')
        rows=examples();rows[1]['id']=rows[0]['id']
        with self.assertRaises(ValueError):fit(rows,'2024-07-01')
        with self.assertRaises(ValueError):fit(examples()[:99],'2024-07-01')

    def test_scaling_only_past_training(self):
        rows=examples();state=fit(rows,'2024-07-01')
        np.testing.assert_allclose(state['mean'],np.array([features(r) for r in rows]).mean(0))
        other=copy.deepcopy(rows[0]);other['market_p']=[.98,.01,.01]
        before=json.dumps(state,sort_keys=True);predict(state,other)
        self.assertEqual(before,json.dumps(state,sort_keys=True))

    def test_bad_features_rejected(self):
        for change in [dict(dc_p=[1,0,0]),dict(market_p=[float('nan'),.2,.3]),dict(unknown_dc_team='False')]:
            with self.assertRaises(ValueError):features({**examples()[0],**change})


if __name__=='__main__':unittest.main()
