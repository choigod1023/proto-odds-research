import unittest,copy
from paired_core import previous,valid_batter,valid_pitcher,build_actual,training_pair
class PairedTest(unittest.TestCase):
 def test_strict_prior_zero_and_complete(self):
  h=[{'date':'2023-01-01','ab':0,'hit':0,'bb':0},{'date':'2023-01-02','ab':1,'hit':0,'bb':None},{'date':'2023-01-03','ab':1,'hit':1,'bb':0}]
  self.assertEqual(previous(h,'2023-01-03',valid_batter),h[:1])
 def test_last_ten_complete(self):
  h=[{'date':f'2023-01-{i:02}','ab':1,'hit':0,'bb':0} for i in range(1,14)];self.assertEqual(previous(h,'2023-01-14',valid_batter),h[-10:])
 def test_pitcher_zero_and_missing(self):
  self.assertTrue(valid_pitcher({'outs':0,'er':0}));self.assertFalse(valid_pitcher({'outs':None,'er':0}))
 def test_target_poison_and_nonplayer_equal(self):
  base={'fixture_id':'T','features':{s+'_'+k:{'value':'old'} for s in ('home','away') for k in ('lineup_strength','starter_strength','team_strength','bullpen_load','rest_days')}};base['features']['park_factor']={'value':'park'}
  roster={'status':'complete','hitters':{s:[{'slot':i,'player_id':str(i+(10 if s=='away' else 0))} for i in range(1,10)] for s in ('home','away')},'starters':{s:{'player_id':'P' if s=='home' else 'Q','actual_record_verified':True} for s in ('home','away')}}
  bh={'1':[{'fixture_id':'old','date':'2023-01-01','ab':0,'hit':0,'bb':0},{'fixture_id':'T','date':'2023-01-02','ab':1,'hit':1,'bb':0},{'fixture_id':'future','date':'2023-01-03','ab':3,'hit':2,'bb':1}]};ph={'P':[{'fixture_id':'old','date':'2023-01-01','outs':3,'er':0},{'fixture_id':'T','date':'2023-01-02','outs':3,'er':3}]};a=build_actual(base,roster,bh,ph,'2023-01-02');poison=copy.deepcopy(bh);poison['1'][1]['hit']=0;poison['1'][2]['bb']=99;b=build_actual(base,roster,poison,ph,'2023-01-02');self.assertEqual(a,b)
  for k in base['features']:
   if not k.endswith(('lineup_strength','starter_strength')):self.assertEqual(a['features'][k],base['features'][k])
 def test_pair_keeps_fixture_and_common_nonplayer_features(self):
  source={'fixture_id':'T','projection':{},'features':{'park_factor':{'value':None}}}
  for side in ('home','away'):
   source['projection'][side+'_lineup']={'player_ids':None};source['projection'][side+'_starter']={'player_id':None}
   for key in ('lineup_strength','starter_strength'):source['features'][side+'_'+key]={'value':None}
   source['features'][side+'_team_strength']={'value':'unchanged'}
  roster={'fixture_id':'T','season':2023,'date':'2023-01-02','teams':{'home':'H','away':'A'},'status':'complete','hitters':{s:[{'slot':i,'player_id':str(i+(10 if s=='away' else 0))} for i in range(1,10)] for s in ('home','away')},'starters':{s:{'player_id':'P' if s=='home' else 'Q','actual_record_verified':True} for s in ('home','away')}}
  raw={'fixture_id':'T','event_date':'2023-01-02','season':2023,'home_id':'H','away_id':'A'}
  class Adapter:
   @staticmethod
   def scalar(name,data):return data
  projected,actual=training_pair(source,raw,roster,{}, {},Adapter())
  self.assertEqual(projected['fixture_id'],actual['fixture_id']);self.assertEqual(projected['raw_features']['home_team_strength'],actual['raw_features']['home_team_strength']);self.assertIsNone(projected['roster']['home']['hitters']);self.assertEqual(len(actual['roster']['home']['hitters']),9)
if __name__=='__main__':unittest.main()
