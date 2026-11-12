import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/kbo-history-20261108e/run.py';s=importlib.util.spec_from_file_location('kbo_history',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def row(target,team='T',gid='g',date='2025-05-01',ids=('1','2')):
 v={'games':[{'id':gid,'date':date,'relief':{'appearances':len(ids),'pitchers':[{'player_id':x} for x in ids]}}]};return {'event_date':target,'home_id':team,'away_id':'Z','raw_features':{'home_bullpen_load':{'value':v},'away_bullpen_load':{'value':None}}}
class KboHistoryTest(unittest.TestCase):
 def test_target_or_future_game_rejected(self):
  with self.assertRaises(AssertionError):m.build_events([row('2025-05-01')])
 def test_conflicting_revisions_removed(self):
  result,n=m.build_events([row('2025-05-05',ids=('1','2')),row('2025-05-06',ids=('1','3'))]);self.assertEqual(n,1);self.assertEqual(len(result['T','g']),2)
 def test_proxy_uses_lag_4_and_rejects_weak_history(self):
  ev={('T',str(i)):{(f'2025-05-{d:02d}',('1','2')):'2025-05-08'} for i,d in enumerate((1,2,3,4,5))}; self.assertEqual(m.proxy(ev,'T','2025-05-09'),4.)
  ev['T','target']={('2025-05-09',('3',)):'2025-05-08'};ev['T','recent']={('2025-05-06',('9',)):'2025-05-08'};self.assertEqual(m.proxy(ev,'T','2025-05-09'),4.)
  ev['T','future_witness']={('2025-05-01',('9',)):'2025-05-09'};self.assertEqual(m.proxy(ev,'T','2025-05-09'),4.)
  del ev['T','0'];self.assertIsNone(m.proxy(ev,'T','2025-05-09'))
 def test_future_conflict_does_not_change_earlier_proxy(self):
  base=[row('2025-05-07',gid='g'+str(i),date=f'2025-05-{i:02d}') for i in range(1,6)]
  original,_=m.build_events(base);before=m.proxy(original,'T','2025-05-09')
  augmented,n=m.build_events(base+[row('2026-01-01',gid='g1',date='2025-05-01',ids=('1','9'))]);self.assertEqual(n,1)
  self.assertEqual(before,m.proxy(augmented,'T','2025-05-09'));self.assertIsNone(m.proxy(augmented,'T','2026-01-02'))
  with_late_conflict,_=m.build_events(base+[row('2025-05-10',gid='g1',date='2025-05-01',ids=('1','9'))]);self.assertEqual(before,m.proxy(with_late_conflict,'T','2025-05-09'));self.assertIsNone(m.proxy(with_late_conflict,'T','2025-05-11'))
 def test_refund_excluded_from_hit_and_roi_returns_zero(self):
  r={'date':'2025-05-01','winner':'draw','arms':{'market':{'p_home':.6,'odds':2.,'side':'home','profit':0}}};s=m.score([r],'market');self.assertEqual(s['refunds'],1);self.assertIsNone(s['accuracy']);self.assertEqual(s['roi'],0)
if __name__=='__main__':unittest.main()
