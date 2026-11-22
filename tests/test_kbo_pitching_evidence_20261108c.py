import importlib.util,unittest
from datetime import date,timedelta
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/kbo-pitching-evidence-20261108c/run.py';s=importlib.util.spec_from_file_location('kbo_starter',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class StarterHistoryTest(unittest.TestCase):
 def data(self):
  target=date(2026,4,15);days={(target-timedelta(days=i)).isoformat():{'games':[]} for i in range(1,11)}
  game={'home':'A','away':'B','appearances':{'A':[{'id':'1','relief':False,'pitches':50}],'B':[{'id':'2','relief':False,'pitches':50}]}}
  days['2026-04-13']['games']=[{'home':'A','away':'B','appearances':{'A':[{'id':'1','pitches':80}],'B':[{'id':'2','pitches':100}]}}]
  return days,game
 def test_strictly_prior_and_player_id(self):
  days,g=self.data();value,reason=m.starter_features(days,'2026-04-15',g);self.assertIsNone(reason);self.assertAlmostEqual(value[0],-20/150)
  g['appearances']['A'][0]['pitches']=9999; self.assertEqual(m.starter_features(days,'2026-04-15',g)[0],value)
 def test_missing_day_and_starter_not_fabricated(self):
  days,g=self.data();del days['2026-04-05'];self.assertEqual(m.starter_features(days,'2026-04-15',g)[1],'missing_prior_day_schedule')
  days,g=self.data();g['appearances']['B'][0]['id']='3';self.assertEqual(m.starter_features(days,'2026-04-15',g)[1],'starter_no_appearance_in_prior_10_days')
 def test_draw_refund_and_daily_drawdown(self):
  rows=[{'date':'2026-04-01','winner':'draw','arms':{'x':{'side':'home','odds':2.,'profit':0.,'prob_home':.6}}},{'date':'2026-04-02','winner':'away','arms':{'x':{'side':'home','odds':2.,'profit':-1.,'prob_home':.6}}}];s=m.score(rows,'x');self.assertEqual((s['refunds'],s['decided'],s['roi'],s['mdd_daily_close']),(1,1,-.5,1.))
class ForwardGateTest(unittest.TestCase):
 def test_rejects_post_t30_capture_before_reading_source(self):
  p=Path(__file__).resolve().parents[1]/'experiments/kbo-pitching-evidence-20261108c/forward_audit.py'; spec=importlib.util.spec_from_file_location('forward_audit',p); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  c={'frozen_at':'2026-11-07T04:31:00+00:00','input':{'event':{'kickoff_at':'2026-11-07T05:00:00+00:00','official_game_id':'fixture'}}}
  with self.assertRaises(AssertionError):mod.inspect_capture(c)
if __name__=='__main__':unittest.main()
