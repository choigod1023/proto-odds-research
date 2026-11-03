import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'common'))
import core

def pred(fid,p):return {'fixture_id':fid,'probabilities_2way':{'home':p,'away':1-p},'probabilities_3way':{'home':.9*p,'draw':.1,'away':.9*(1-p)}}
class SelectionSafety(unittest.TestCase):
 def setUp(self):
  self.labels={'x':{'fixture_id':'x','assumed_completed_date':'2025-04-01','home_score':1,'away_score':0},'y':{'fixture_id':'y','assumed_completed_date':'2025-04-02','home_score':0,'away_score':1}}
  self.base=[pred('x',.6),pred('y',.6)];self.candidate=[pred('x',.4),pred('y',.2)]
 def test_subset_is_currently_accepted_and_changes_winner(self):
  self.assertEqual(core.choose({'baseline':self.base,'candidate':self.candidate},self.labels)['selected'],'candidate')
  self.assertEqual(core.choose({'baseline':self.base[:1],'candidate':self.candidate[:1]},self.labels)['selected'],'baseline')
 def test_different_candidate_fixture_sets_rejected(self):
  with self.assertRaises(ValueError):core.choose({'baseline':self.base,'candidate':self.candidate[:1]},self.labels)
 def test_non_2025_selection_rejected(self):
  self.labels['x']['assumed_completed_date']='2026-04-01'
  with self.assertRaises(ValueError):core.choose({'baseline':self.base},self.labels)
 def test_baseline_tie_before_lexical(self):
  self.assertEqual(core.choose({'z':self.base,'a':self.base,'baseline':self.base},self.labels)['ranking'],['baseline','a','z'])
 def test_no_baseline_rejected(self):
  with self.assertRaises(ValueError):core.choose({'candidate':self.candidate},self.labels)
if __name__=='__main__':unittest.main()
