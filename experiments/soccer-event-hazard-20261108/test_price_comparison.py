import unittest
import price_comparison as p

class PriceComparisonTest(unittest.TestCase):
    def setUp(self):
        self.fixture={'id':1,'date':'2016-03-05','kickoff':'12:00','home':'Arsenal','away':'Chelsea','home_score':2,'away_score':1}
        self.pred={'id':1,'date':'2016-03-05','result':0,'poisson':[.6,.2,.2]}
        self.price={'Date':'2016-03-05','HomeTeam':'Arsenal','AwayTeam':'Chelsea','FTHG':'2','FTAG':'1','FTR':'H','B365H':'2','B365D':'3','B365A':'4'}
    def test_price_and_settlement(self):
        rows,excluded=p.join([self.pred],[self.fixture],[self.price]); self.assertFalse(excluded)
        self.assertAlmostEqual(sum(rows[0]['market']),1)
        self.assertEqual(p.profit(rows[0],'poisson'),1)
    def test_reject_score_and_duplicate(self):
        bad={**self.price,'FTHG':'1'}
        self.assertEqual(p.join([self.pred],[self.fixture],[bad])[1][0]['reason'],'score_mismatch')
        self.assertEqual(p.join([self.pred],[self.fixture],[self.price,self.price])[1][0]['reason'],'ambiguous_duplicate')
    def test_no_future_price_or_missing_odds(self):
        bad={**self.price,'B365A':''}
        self.assertEqual(len(p.join([self.pred],[self.fixture],[bad])[0]),0)
        bad={**self.price,'Date':'2016-03-06'}
        self.assertEqual(p.join([self.pred],[self.fixture],[bad])[1][0]['reason'],'no_match')
    def test_daily_drawdown(self):
        rows=[{'date':'2016-03-01','result':0,'odds':[2,3,4],'poisson':[1,0,0]}, {'date':'2016-03-02','result':1,'odds':[2,3,4],'poisson':[1,0,0]}]
        self.assertEqual(p.metrics(rows,'poisson')['daily_close_mdd_units'],1)

if __name__=='__main__': unittest.main()
