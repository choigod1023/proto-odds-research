import unittest
from datetime import datetime, timezone, timedelta
from audit_official_prices import usable, candidates


class OfflineOfficialPriceAuditTests(unittest.TestCase):
    def setUp(self):
        self.row={'betId':None,'betTypId':None,'handi':0,'winAllot':1.36,'loseAllot':2.49,
                  'leagueCode':'BS001','itemCode':'BS','winHandi':0,'loseHandi':0,'drawAllot':0,
                  'gameSubject':None,'gameReject':'0','buyReject':'0'}

    def test_market_identity_rejects_spread_and_non_kbo(self):
        self.assertTrue(usable(self.row))
        modern=self.row|{'betId':'2','betTypId':'2','handi':21,'betNm':'야구 승패','betTypNm':'일반 승패'}
        self.assertTrue(usable(modern))
        for change in ({'handi':-1.5},{'leagueCode':'BS002'},{'betId':'7'},
                       {'betTypNm':'핸디캡'},{'winHandi':1.5},{'gameReject':'1'},
                       {'buyReject':'1'},{'winAllot':1.0},{'drawAllot':3.0}):
            self.assertFalse(usable(modern|change))

    def test_clock_missing_and_duplicate_candidates_are_not_disambiguated(self):
        g={'date':'2023-04-14','home':'KT','away':'한화','hg':7,'ag':7}
        t=datetime(2023,4,14,18,30,tzinfo=timezone(timedelta(hours=9)))
        bank={('2023-04-14','KT','HH','7:7'):[(1,'a',{},t),(2,'b',{},t)]}
        self.assertEqual(len(candidates(bank,g,None)),2)
        self.assertEqual(len(candidates(bank,g,t.isoformat())),2)
        self.assertEqual(len(candidates(bank,g,(t+timedelta(hours=1)).isoformat())),0)


if __name__ == '__main__':
    unittest.main()
