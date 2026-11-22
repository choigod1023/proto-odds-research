import unittest
from join_prices import market_win
class PriceTests(unittest.TestCase):
 def legacy(self):return {'betId':None,'betTypId':None,'handi':0,'winHandi':0,'drawHandi':0,'loseHandi':0,'winAllot':1.6,'drawAllot':0,'loseAllot':2.0}
 def test_general(self):self.assertTrue(market_win(self.legacy()))
 def test_middle_five_not_draw(self):
  a=self.legacy();a.update(handi=14,drawAllot=2.5);self.assertFalse(market_win(a))
 def test_handicap_or_total_not_general(self):
  for h in (2,9):
   a=self.legacy();a['handi']=h;self.assertFalse(market_win(a))
 def test_missing_or_refund_price(self):
  a=self.legacy();a['winAllot']=1;self.assertFalse(market_win(a))
if __name__=='__main__':unittest.main()
