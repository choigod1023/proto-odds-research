"""Numerical/causal guards and independent settlement audit of both candidates."""
import copy,gzip,json,math,sys,unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import numpy as np
import train
import evaluate

POWER=train.ROOT/'experiments/kbo-m0-power-20261103'
sys.path.insert(0,str(POWER))
import train_power,evaluate_power

class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=train.core.rows('train-actual.jsonl.gz')
        cls.targets=train.core.rows('test-actual.jsonl.gz')
        cls.frozen={y:evaluate.artifact(y) for y in (2024,2025,2026)}
        cls.power={y:evaluate_power.artifact(y) for y in (2024,2025,2026)}
        cls.labels,cls.prices,cls.strict,cls.source=evaluate.evaluation_inputs(cls.frozen)

    def test_logistic_gradient(self):
        rng=np.random.default_rng(84);X=rng.normal(size=(19,8));b=rng.normal(size=8);offset=rng.normal(size=19);y=rng.integers(0,2,19);pen=np.r_[0,np.full(7,10.)]
        loss,grad=train.objective(b,X,offset,y,pen);numeric=[]
        for j in range(len(b)):
            delta=np.zeros_like(b);delta[j]=1e-5
            numeric.append((train.objective(b+delta,X,offset,y,pen)[0]-train.objective(b-delta,X,offset,y,pen)[0])/2e-5)
        np.testing.assert_allclose(grad,numeric,atol=1e-7,rtol=1e-7)

    def test_zero_residual_preserves_baseline(self):
        year=2024;rows=sorted([r for r in self.rows if r['season']==year],key=lambda r:(r['event_date'],r['fixture_id']))
        model=json.loads((train.P/f'artifacts/{year}/models.json').read_bytes())['winner_head'];model['beta']=[0.]*len(model['beta'])
        pred=train.predict(model,self.frozen[year]['baseline'],rows)
        for p,b in zip(pred,self.frozen[year]['baseline']):
            self.assertEqual(p['probabilities_3way']['draw'],b['probabilities_3way']['draw'])
            self.assertAlmostEqual(p['probabilities_2way']['home'],b['probabilities_2way']['home'],places=14)

    def test_forecast_identity_mismatch_rejected(self):
        rows=[self.rows[0]];wrong=[{'fixture_id':'wrong'}]
        with self.assertRaisesRegex(ValueError,'identity'):train.predict({},wrong,rows)
        with self.assertRaisesRegex(ValueError,'identity'):train_power.predict({},rows,wrong)

    def synthetic(self):
        h={'date':'2026-06-01','fixture_id':'past','ab':4,'hr':1}
        p={'player_id':'p1','history':[h]}
        return {'fixture_id':'target','event_date':'2026-06-02','raw_features':{'home_lineup_strength':{'value':[p]},'away_lineup_strength':{'value':None}}}

    def test_power_smoothing_and_missing(self):
        r=self.synthetic();self.assertAlmostEqual(train_power.power(r,'home',.05),2/24)
        self.assertTrue(math.isnan(train_power.power(r,'away',.05)))
        r['raw_features']['home_lineup_strength']['value'][0]['history'][0]['ab']=True
        self.assertTrue(math.isnan(train_power.power(r,'home',.05)))

    def test_future_or_same_fixture_rejected(self):
        for day,fid in (('2026-06-02','past'),('2026-06-03','past'),('2026-06-01','target')):
            r=self.synthetic();h=r['raw_features']['home_lineup_strength']['value'][0]['history'][0];h.update(date=day,fixture_id=fid)
            with self.assertRaisesRegex(ValueError,'before target'):train_power.power(r,'home',.05)

    def test_prior_deduplicates_and_rejects_conflict(self):
        r=self.synthetic();self.assertEqual(train_power.fit_prior([r,r]),(.25,1))
        other=copy.deepcopy(r);other['raw_features']['home_lineup_strength']['value'][0]['history'][0]['hr']=0
        with self.assertRaisesRegex(ValueError,'conflicting'):train_power.fit_prior([r,other])

    def test_frozen_predictions_reproduce_after_causal_guards(self):
        for year in (2024,2025,2026):
            with self.subTest(year=year):
                rows=self.targets if year==2026 else [r for r in self.rows if r['season']==year]
                rows=sorted(rows,key=lambda r:(r['event_date'],r['fixture_id']))
                model=json.loads((POWER/f'artifacts/{year}/model.json').read_bytes())
                actual=train_power.predict(model,rows,self.power[year]['baseline'])
                self.assertEqual(actual,self.power[year]['power_m0'])
                head=json.loads((train.P/f'artifacts/{year}/models.json').read_bytes())['winner_head']
                self.assertEqual(train.predict(head,self.frozen[year]['baseline'],rows),self.frozen[year]['winner_head'])

    def test_zero_power_coefficients_match_m0(self):
        rows=sorted(self.targets,key=lambda r:(r['event_date'],r['fixture_id']))[:5]
        baseline=json.loads((train.P/'artifacts/2026/models.json').read_bytes())['baseline']
        model=json.loads((POWER/'artifacts/2026/model.json').read_bytes());model['beta']=baseline['beta']+[0.,0.]
        base=self.frozen[2026]['baseline'][:5];actual=train_power.predict(model,rows,base)
        for p,b in zip(actual,base):
            for k in ('home','away'):self.assertAlmostEqual(p['probabilities_2way'][k],b['probabilities_2way'][k],places=12)

    def test_training_prior_scaler_and_embargo(self):
        for year in (2024,2025,2026):
            with self.subTest(year=year):
                rows=sorted([r for r in self.rows if r['season']<year],key=lambda r:(r['event_date'],r['fixture_id']))
                target=sorted(self.targets if year==2026 else [r for r in self.rows if r['season']==year],key=lambda r:(r['event_date'],r['fixture_id']))
                self.assertGreaterEqual((train.date.fromisoformat(target[0]['event_date'])-train.date.fromisoformat(rows[-1]['event_date'])).days,7)
                self.assertFalse(set(train.core.unique(rows))&set(self.labels[year]))
                model=json.loads((POWER/f'artifacts/{year}/model.json').read_bytes());prior,n=train_power.fit_prior(rows)
                self.assertEqual((prior,n),(model['prior'],model['prior_unique_observations']))
                _,mean,scale=train_power.columns(rows,prior);self.assertEqual((mean,scale),(model['mean'],model['scale']))

    def test_independent_decimal_settlement_and_hits(self):
        # No metrics.settle/pick/winner call: recompute every priced return directly.
        for folder,forecasts in ((train.P,self.frozen),(POWER,self.power)):
            result=json.loads((folder/'results.json').read_bytes())
            for year in (2024,2025,2026):
                for name,predictions in forecasts[year].items():
                    with self.subTest(folder=folder.name,year=year,model=name):
                        index={p['fixture_id']:p for p in predictions};hits=draws=0;net=Decimal(0);daily=defaultdict(Decimal)
                        self.assertEqual(len(index),len(predictions));self.assertEqual(set(index),set(self.labels[year]))
                        for fid,y in self.labels[year].items():
                            truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
                            q=index[fid]['probabilities_2way'];side='away' if q['away']>q['home'] else 'home';hits+=side==truth;draws+=truth=='draw'
                        m=result['annual'][str(year)]['models'][name]
                        self.assertEqual((hits,len(index)-draws),(m['outcome']['decisive_hits'],m['outcome']['decisive']))
                        priced_ids=set()
                        for p in self.prices[year]:
                            fid=p['fixture_id'];self.assertNotIn(fid,priced_ids);priced_ids.add(fid);y=self.labels[year][fid]
                            truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
                            q=index[fid]['probabilities_2way'];side='away' if q['away']>q['home'] else 'home'
                            z=Decimal(0) if truth=='draw' else Decimal(str(p['price_source']['prices'][side]))-1 if side==truth else Decimal(-1)
                            net+=z;daily[p['date']]+=z
                        self.assertEqual(str(net),m['price']['net_units']);self.assertAlmostEqual(float(net/len(priced_ids)),m['price']['roi'],places=14)
                        balance=peak=dd=Decimal(0)
                        for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
                        self.assertEqual(str(dd),m['price']['max_daily_close_drawdown_units'])

if __name__=='__main__':unittest.main()
