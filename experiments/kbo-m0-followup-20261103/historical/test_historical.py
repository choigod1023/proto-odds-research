import copy,math,unittest
import run

def p(fid,h,d,a):return {'fixture_id':fid,'event_date':'2024-04-01','probabilities_3way':{'home':h,'draw':d,'away':a},'probabilities_2way':{'home':h/(h+a),'away':a/(h+a)}}
class HistoricalTests(unittest.TestCase):
 def test_poisoned_future_label(self):
  labels=run.PoisonLabels({'2023':{'home_score':1}})
  self.assertEqual(labels['2023']['home_score'],1)
  with self.assertRaises(AssertionError):labels['2024']
 def test_ensemble_unconditional_first(self):
  x={'baseline':[p('x',.45,.1,.45)],'shrink':[p('x',.2,.6,.2)],'nb03':[p('x',.6,.1,.3)]}
  z=run.ensemble(x)[0];self.assertAlmostEqual(z['probabilities_3way']['home'],1.25/3);self.assertAlmostEqual(z['probabilities_2way']['home'],1.25/(1.25+.95))
  self.assertNotAlmostEqual(z['probabilities_2way']['home'],sum(v[0]['probabilities_2way']['home'] for v in x.values())/3)
 def test_invalid_and_duplicate(self):
  a=p('x',.4,.2,.4);bad=copy.deepcopy(a);bad['probabilities_2way']={'home':.9,'away':.1}
  with self.assertRaises(ValueError):run.validate([bad],{'x'})
  bad=copy.deepcopy(a);bad['probabilities_3way']['home']=float('nan')
  with self.assertRaises(ValueError):run.validate([bad],{'x'})
  with self.assertRaises(ValueError):run.validate([a,a],{'x','y'})
 def test_train_only_scaler_and_input_guards(self):
  train,target,labels=run.inputs();before=run.core.legacy.make_spec([run.core.normalize(r) for r in train]);changed=copy.deepcopy(target[0]);changed['features']['home_team_strength']['value']=1e8
  after=run.core.legacy.make_spec([run.core.normalize(r) for r in train]);polluted=run.core.legacy.make_spec([run.core.normalize(r) for r in train]+[run.core.normalize(changed)])
  self.assertEqual(before,after);self.assertNotEqual(before['mean'],polluted['mean']);self.assertEqual(len(labels),723)
if __name__=='__main__':unittest.main()
