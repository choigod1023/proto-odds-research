import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'common'))
import core

def pred(fid,p):return {'fixture_id':fid,'probabilities_2way':{'home':p,'away':1-p},'probabilities_3way':{'home':p*.9,'draw':.1,'away':(1-p)*.9}}
class SelectionSafety(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  d=core.load();cls.labels=d['labels'];cls.base=core.baseline_reference()['development_predictions']
 def test_subset_is_now_rejected(self):
  with self.assertRaisesRegex(ValueError,'complete fixed2025'):core.choose({'baseline':self.base[:-1]},self.labels)
 def test_full_cohort_accepted(self):
  self.assertEqual(core.choose({'baseline':self.base},self.labels)['metrics']['baseline']['n'],731)
 def test_different_candidate_fixture_sets_rejected(self):
  with self.assertRaisesRegex(ValueError,'populations differ'):core.choose({'baseline':self.base,'candidate':self.base[:-1]},self.labels)
 def test_baseline_tie_before_lexical(self):
  self.assertEqual(core.choose({'z':self.base,'a':self.base,'baseline':self.base},self.labels)['ranking'],['baseline','a','z'])
 def test_no_baseline_rejected(self):
  with self.assertRaises(ValueError):core.choose({'candidate':self.base},self.labels)
 def test_bad_probabilities_and_duplicate_rejected(self):
  for mutation in ('nan','sum','conditional','duplicate'):
   p=copy.deepcopy(self.base)
   if mutation=='nan':p[0]['probabilities_2way']['home']=float('nan')
   if mutation=='sum':p[0]['probabilities_3way']['draw']=.99
   if mutation=='conditional':p[0]['probabilities_2way']={'home':.99,'away':.01}
   if mutation=='duplicate':p.append(p[0])
   with self.assertRaises(ValueError):core.choose({'baseline':p},self.labels)
if __name__=='__main__':unittest.main()
