import copy,math,unittest
import candidate

def row():
 line=[{'player_id':str(i),'history':[]} for i in range(9)]
 line[0]['history']=[{'date':'2024-12-02','ab':10,'hit':5,'bb':0}]
 pitch={'projected_player_id':'p','appearances':[{'date':'2024-12-02','er':2,'outs':9}]}
 features={};raw={}
 for s in ('home','away'):
  for suffix,val,scalar in [('lineup_strength',line,.5),('starter_strength',pitch,6.)]:
   name=s+'_'+suffix;raw[name]={'value':copy.deepcopy(val)};features[name]={'value':scalar}
 return {'fixture_id':'x','event_date':'2025-01-01','season':2025,'home_id':'h','away_id':'a','features':features,'roster':{},'raw_features':raw}

class Tests(unittest.TestCase):
 def test_decay_and_no_mutation(self):
  r=row(); before=copy.deepcopy(r); x=candidate.transform(r,'decay30');self.assertEqual(r,before)
  self.assertAlmostEqual(x['features']['home_lineup_strength']['value'],.5)
  self.assertAlmostEqual(x['features']['home_starter_strength']['value'],6.)
 def test_shrink_and_order(self):
  r=row();x=candidate.transform(r,'shrink');self.assertAlmostEqual(x['features']['home_lineup_strength']['value'],(5+9*.33*20)/(10+9*20));self.assertAlmostEqual(x['features']['home_starter_strength']['value'],(2+4.5)*27/36)
  x=candidate.transform(r,'ordered_decay30_shrink');p=(2.5+.33*20)/(5+20);expected=(p*4.7+sum(candidate.ORDER_WEIGHTS[1:])*.33)/4.3/9
  self.assertAlmostEqual(x['features']['home_lineup_strength']['value'],expected)
  self.assertAlmostEqual(x['features']['home_starter_strength']['value'],(1+4.5)*27/(4.5+27))
 def test_nulls_stay_null(self):
  r=row()
  for name in ('home_lineup_strength','home_starter_strength'):r['features'][name]['value']=None
  for cfg in candidate.CONFIGS:
   x=candidate.transform(r,cfg)
   self.assertIsNone(x['features']['home_lineup_strength']['value']);self.assertIsNone(x['features']['home_starter_strength']['value'])
 def test_future_history_rejected(self):
  r=row();r['raw_features']['home_lineup_strength']['value'][0]['history'][0]['date']='2025-01-01'
  with self.assertRaises(ValueError):candidate.transform(r,'decay30')
if __name__=='__main__':unittest.main()
