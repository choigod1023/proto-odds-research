import copy
import datetime as dt
import unittest
from resolve import resolve_one, index_unique, KST

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.f={'fixture_id':'score:example','event_date':'2026-07-28','home_id':'LG','away_id':'키움'}
        self.gid='20260728WOLG0'
        self.s={'official_schedule_pitcher_context':{'official_game_id':self.gid,'schedule_evidence':{'sha256':'abc'},'pitchers':{'home':{'team_id':'LG'},'away':{'team_id':'WO'}}},'official_lineup_context':{'official_game_id':self.gid,'home_team_id':'LG','away_team_id':'WO'}}
        self.st={'official_game_id':self.gid}
        self.game={'G_DT':'20260728','HOME_ID':'LG','AWAY_ID':'WO','G_ID':self.gid,'HEADER_NO':0,'G_TM':'18:30'}
        self.clock=dt.datetime(2026,7,28,18,30,tzinfo=KST)
        self.quotes={self.clock:[{'round':'260090','body_sha256':'abc','body_path':'archive'}]}
    def check(self,games=None,quotes=None):
        return resolve_one(self.f,self.s,self.st,[self.game] if games is None else games,self.quotes if quotes is None else quotes)
    def test_exact_match(self):
        self.assertEqual(self.check()['official_game_id'],self.gid)
        self.assertEqual(self.check()['kickoff'],'2026-07-28T18:30:00+09:00')
    def test_team_orientation_conflict(self):
        self.s['official_lineup_context']['away_team_id']='KT'
        with self.assertRaisesRegex(ValueError,'identity_conflict'): self.check()
    def test_doubleheader_and_duplicate_schedule_rejected(self):
        self.game['HEADER_NO']=1
        with self.assertRaisesRegex(ValueError,'doubleheader'): self.check()
        self.game['HEADER_NO']=0
        with self.assertRaisesRegex(ValueError,'schedule_not_unique'): self.check([self.game,copy.deepcopy(self.game)])
    def test_clock_mismatch_and_multiple_quotes_rejected(self):
        self.game['G_TM']='18:00'
        with self.assertRaisesRegex(ValueError,'kickoff_mismatch'): self.check()
        self.game['G_TM']='18:30'; q=dict(self.quotes); q[self.clock+dt.timedelta(minutes=10)]=[]
        with self.assertRaisesRegex(ValueError,'betman_kickoff_not_unique'): self.check(quotes=q)
    def test_duplicate_fixture_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate fixture_id'): index_unique([self.f,self.f])

if __name__=='__main__': unittest.main()
