import copy,unittest
from scripts.export_nba_player_shots import classify,digest,gzip_rows,hard,minutes,shot_actor,soft,validate_pregame

class PlayerShotTests(unittest.TestCase):
    def evidence(self):
        source=b'confirmed lineup';return {'kind':'confirmed_pregame_lineup','source_sha256':digest(source),'game_id':'g','teams':{'home':[str(i) for i in range(5)],'away':[str(i) for i in range(5,10)]},'captured_at':'2025-01-01T10:00:00Z','published_at':'2025-01-01T09:55:00Z'},source
    def test_pregame_guard_blocks_late_capture_or_publication(self):
        e,b=self.evidence();self.assertTrue(validate_pregame(e,'2025-01-01T10:00:00Z','2025-01-01T10:30:00Z',b,'g'))
        for k in ('captured_at','published_at'):
            bad=copy.deepcopy(e);bad[k]='2025-01-01T10:00:01Z'
            with self.assertRaises(ValueError):validate_pregame(bad,'2025-01-01T10:00:00Z','2025-01-01T10:30:00Z',b,'g')
    def test_target_actual_minutes_starter_forbidden(self):
        e,b=self.evidence()
        for k in ('actual_minutes','minutes_seconds_displayed','starter_postgame','target_actual_starter','actual_starter'):
            bad=copy.deepcopy(e);bad[k]=33
            with self.assertRaises(ValueError):validate_pregame(bad,'2025-01-01T10:00:00Z','2025-01-01T10:30:00Z',b,'g')
        with self.assertRaises(ValueError):validate_pregame(e,'2025-01-01T10:00:00Z','2025-01-01T10:30:00Z',b'altered','g')
    def test_shooter_not_assister_or_blocker(self):
        players={'s':{'name':'Derrick White'}};p={'text':"Scottie Barnes blocks Derrick White 's 9-foot layup",'participants':[{'athlete':{'id':'s'}},{'athlete':{'id':'b'}}]}
        self.assertEqual(shot_actor(p,players),('s',None));p['participants'].reverse();self.assertEqual(shot_actor(p,players),(None,'first_participant_not_on_team'))
        p={'text':'Other Player makes layup (Derrick White assists)','participants':[{'athlete':{'id':'s'}}]};self.assertEqual(shot_actor(p,players),(None,'name_id_disagreement'))
    def test_coordinates_conserve_family_and_missing(self):
        for n in (2,3):
            for r in (0,4,10,16,24,27,40):self.assertAlmostEqual(sum(soft(n,{'x':25+r,'y':0}).values()),1)
            self.assertEqual(soft(n,None),{4 if n==2 else 8:1.0})
        self.assertEqual(hard(2,{'x':29,'y':0}),1)
    def test_minutes_precision_and_overtime_possible(self):
        self.assertEqual(minutes('31'),(1860,60));self.assertEqual(minutes('31:45'),(1905,1));self.assertEqual(minutes('--'),(None,None));self.assertEqual(minutes('51'),(3060,60))
    def test_deterministic_output(self):self.assertEqual(gzip_rows([{'a':1}]),gzip_rows([{'a':1}]))
if __name__=='__main__':unittest.main()
