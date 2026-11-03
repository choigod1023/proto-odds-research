import copy
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import check_grad, minimize
from scipy.special import expit, logit
from scipy.stats import nbinom, poisson

import candidate


class DistributionTests(unittest.TestCase):
    def test_nb2_gradient_and_log_likelihood(self):
        rng = np.random.default_rng(20261103)
        X = np.column_stack([np.ones(30),rng.normal(size=(30,4))])
        y = rng.poisson(4,size=30)
        beta = rng.normal(scale=0.15,size=5)
        penalty = np.array([0.,10.,10.,10.,10.])
        for alpha in (0.1,0.3):
            f=lambda b:candidate.objective(b,X,y,penalty,alpha)[0]
            g=lambda b:candidate.objective(b,X,y,penalty,alpha)[1]
            self.assertLess(check_grad(f,g,beta),2e-5)
            mu=np.exp(X@beta); n=1/alpha; p=n/(n+mu)
            delta = f(beta)+np.sum(nbinom.logpmf(y,n,p))-0.5*np.dot(penalty*beta,beta)
            beta2=beta+0.1
            mu2=np.exp(X@beta2);p2=n/(n+mu2)
            delta2=f(beta2)+np.sum(nbinom.logpmf(y,n,p2))-0.5*np.dot(penalty*beta2,beta2)
            self.assertAlmostEqual(delta,delta2,places=9)

    def test_poisson_limit_and_refit(self):
        rng=np.random.default_rng(44)
        X=np.column_stack((np.ones(250),rng.normal(size=(250,3))))
        y=rng.poisson(np.exp(X@np.array([1.3,0.2,-0.1,0.15])))
        penalty=np.array([0.,10.,10.,10.])
        def pfit(b):
            z=X@b;mu=np.exp(z)
            return float(np.sum(mu-y*z)+0.5*np.dot(penalty*b,b)),X.T@(mu-y)+penalty*b
        init=np.zeros(4); init[0]=np.log(y.mean())
        p=minimize(pfit,init,jac=True,method='BFGS',options={'gtol':1e-8})
        n=minimize(candidate.objective,init,args=(X,y,penalty,1e-8),jac=True,method='BFGS',options={'gtol':1e-8})
        np.testing.assert_allclose(p.x,n.x,atol=2e-7,rtol=0)
        np.testing.assert_allclose(nbinom.pmf(np.arange(20),1e8,1/(1+1e-8*4.2)),poisson.pmf(np.arange(20),4.2),atol=2e-8)

    def test_margins_outcomes_and_tail(self):
        for alpha in (0.1,0.3):
            h=np.array([1.,4.2,10.]); a=np.array([5.,3.9,11.])
            probs,tail,high=candidate.nb_outcomes(h,a,alpha)
            self.assertLessEqual(tail,candidate.TAIL_TOL*1.01)
            for i in range(len(h)):
                n=1/alpha;ph=1/(1+alpha*h[i]);pa=1/(1+alpha*a[i])
                mean,var=nbinom.stats(n,ph,moments='mv')
                self.assertAlmostEqual(mean,h[i],places=10)
                self.assertAlmostEqual(var,h[i]+alpha*h[i]**2,places=10)
                k=np.arange(high+1);hpmf=nbinom.pmf(k,n,ph);apmf=nbinom.pmf(k,n,pa)
                direct=np.array([np.dot(hpmf,nbinom.cdf(k-1,n,pa)),np.dot(hpmf,apmf),np.dot(apmf,nbinom.cdf(k-1,n,ph))])
                direct/=direct.sum()
                np.testing.assert_allclose(probs[i],direct,atol=1e-13,rtol=0)

    def test_hurdle_uses_training_labels_and_keeps_conditional_probabilities(self):
        rows=[{'fixture_id':str(i),'event_date':'2024-01-01','season':2024,'home_id':'H','away_id':'A','features':{},'roster':{}} for i in range(4)]
        labels={str(i):{'home_score':1,'away_score':1 if i==0 else 0} for i in range(4)}
        base=[{'fixture_id':str(i),'event_date':'2024-01-01','probabilities_3way':{'home':0.48,'draw':0.2,'away':0.32},'probabilities_2way':{'home':0.6,'away':0.4}} for i in range(4)]
        original=copy.deepcopy((rows,labels,base))
        with patch.object(candidate.core,'fit_baseline',return_value={'fake':1}), patch.object(candidate.core,'predict_baseline',return_value=base):
            model=candidate.fit(rows,labels,'draw_hurdle');result=candidate.predict(model,rows)
        self.assertAlmostEqual(np.mean([r['probabilities_3way']['draw'] for r in result]),1.5/5)
        self.assertEqual([r['probabilities_2way'] for r in result],[r['probabilities_2way'] for r in base])
        self.assertEqual((rows,labels,base),original)
        for r in result:self.assertAlmostEqual(sum(r['probabilities_3way'].values()),1.0)


if __name__ == '__main__':
    unittest.main()
