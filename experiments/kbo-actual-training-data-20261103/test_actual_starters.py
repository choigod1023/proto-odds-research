import copy,unittest
import collect_actual_starters as target

def sample():
 def table(name):
  return {'headers':[{'row':[{'Text':'선수명'},{'Text':'등판'}]}],'rows':[{'row':[{'Text':name},{'Text':'선발'}]},{'row':[{'Text':'B'},{'Text':'4.1'}]}]}
 game={'GAME_STATE_SC':'3','GAME_RESULT_CK':'1','HOME_ID':'HT','AWAY_ID':'AT','B_PIT_P_ID':'101','T_PIT_P_ID':'102','B_PIT_P_NM':'A','T_PIT_P_NM':'C'}
 source={'starting_pitchers':{'home':{'player_id':'101','name':'A'},'away':{'player_id':'102','name':'C'}}}
 return {'code':'100','tables':[{}, {}, {},table('C'),table('101')]},game,source
class ActualStarterTest(unittest.TestCase):
 def test_official_flag_schedule_match(self):
  b,g,s=sample();r=target.parse_box(b,g,s);self.assertEqual(r['home']['player_id'],'101');self.assertTrue(r['away']['actual_record_verified'])
 def test_schedule_alone_is_insufficient(self):
  b,g,s=sample();b['tables'][3]['rows'][0]['row'][1]['Text']='1.1'
  with self.assertRaises(ValueError):target.parse_box(b,g,s)
 def test_nonmatching_names_or_ids_are_excluded(self):
  b,g,s=sample();b['tables'][3]['rows'][0]['row'][0]['Text']='different'
  with self.assertRaises(ValueError):target.parse_box(b,g,s)
 def test_changed_actual_is_retained_only_with_profile_resolver(self):
  b,g,s=sample();b['tables'][3]['rows'][0]['row'][0]['Text']='Changed'
  r=target.parse_box(b,g,s,lambda side,claim:('103','Changed',{'sha256':'verified_by_collector'}))
  self.assertEqual(r['away']['player_id'],'103');self.assertFalse(r['away']['evidence']['schedule_matches_actual'])
 def test_duplicate_and_nonfirst_starter_excluded(self):
  b,g,s=sample();b['tables'][4]['rows'][1]['row'][1]['Text']='선발'
  with self.assertRaises(ValueError):target.parse_box(b,g,s)
  b,g,s=sample();b['tables'][4]['rows'].reverse()
  with self.assertRaises(ValueError):target.parse_box(b,g,s)
 def test_unfinished_game_excluded(self):
  b,g,s=sample();g['GAME_RESULT_CK']='0'
  with self.assertRaises(ValueError):target.parse_box(b,g,s)
if __name__=='__main__':unittest.main()
