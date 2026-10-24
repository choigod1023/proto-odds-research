import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from edge_diagnosis import calibrate_fit,calibrate,choose_weight,tickets,diagnose,GRID
from pregame_evidence_gate import check_evidence


class EdgeTests(unittest.TestCase):
    def test_asof_gate_rejects_missing_late_naive_and_wrong_event(self):
        pred='2025-01-01T09:00:00Z';kick='2025-01-01T10:00:00Z'
        r={'event_id':'x','observed_at':'2025-01-01T08:00:00Z'}
        self.assertEqual(check_evidence(r,'x',pred,kick),'eligible')
        self.assertEqual(check_evidence(r,'y',pred,kick),'event_mismatch')
        self.assertEqual(check_evidence({'event_id':'x'},'x',pred,kick),'missing_or_naive_observation_time')
        r['observed_at']='2025-01-01T09:01:00Z'
        self.assertEqual(check_evidence(r,'x',pred,kick),'observed_after_prediction')
        r['observed_at']='2025-01-01T08:00:00'
        self.assertEqual(check_evidence(r,'x',pred,kick),'missing_or_naive_observation_time')
        r['observed_at']='2025-01-01T08:00:00Z'
        self.assertEqual(check_evidence(r,'x',kick,kick),'prediction_not_pregame')

    def test_calibration_probabilities_and_no_labels_at_prediction(self):
        p=np.tile([.8,.2],(100,1));y=np.array([0]*60+[1]*40)
        t=calibrate_fit(p,y);q=calibrate(t,p)
        self.assertTrue(np.isfinite(q).all());np.testing.assert_allclose(q.sum(1),1)
        self.assertLess(abs(q[:,0].mean()-.6),abs(p[:,0].mean()-.6))

    def test_accuracy_constraint_and_tie(self):
        scores={w:dict(pair_accuracy=.6,roi=-.1) for w in GRID}
        scores[1.]=dict(pair_accuracy=.58,roi=.1)
        self.assertEqual(choose_weight(scores,0),0)
        self.assertEqual(choose_weight(scores,.02),1)

    def fixture(self):
        return pd.DataFrame(dict(event=[0,1,2],choice=[0]*3,date=[pd.Timestamp('2025-01-01')]*3,
            sport=['bs']*3,league=['A']*3,home=['a','b','c'],away=['d','e','f'],
            price=[1.5,1.6,3],p=[.8,.7,.4],q=[.75,.65,.35],v=[0]*3,ev=[.2,.12,.2],hit=[1,0,1]))

    def test_same_legs_single_and_parlay_accounting(self):
        t=tickets(self.fixture(),0)[0]
        self.assertEqual(t['events'],[0,1]);self.assertAlmostEqual(t['single_roi'],-.25)
        self.assertEqual(t['return_'],-1);self.assertAlmostEqual(t['predicted_roi'],.8*.7*1.5*1.6-1)

    def test_weights_zero_preserves_probability_order(self):
        d=self.fixture();a=tickets(d,0)[0];d.hit=1-d.hit;b=tickets(d,0)[0]
        self.assertEqual(a['events'],b['events']);self.assertEqual(a['choices'],b['choices'])
        self.assertEqual(set(tickets(d,1)[0]['events']),{0,2})

    def test_diagnostics_count_all_options_and_events_separately(self):
        d=pd.DataFrame(dict(market=[[.6,.4],[.6,.4]],odds=[[1.5,2],[1.5,2]],y=[0,1]))
        r=diagnose(d,np.array([[.6,.4],[.6,.4]]))
        self.assertEqual(r['n'],2);self.assertEqual(r['bins']['odds_1.5_2.2']['n'],4)
        self.assertEqual(r['positive_ev_options'],0);self.assertEqual(r['accuracy'],.5)
