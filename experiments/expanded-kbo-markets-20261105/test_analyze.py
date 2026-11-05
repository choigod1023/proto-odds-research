import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('analysis',Path(__file__).with_name('analyze.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Checks(unittest.TestCase):
    def setUp(self):
        self.w={'protoStatus':'4','gameReject':'0','mchScore':'5:5','gameResult':'4'}
        self.r={'protoStatus':'4','gameReject':'0','gameSubject':None,'drawAllot':0,'winHandi':-2.5,'loseHandi':2.5,'winAllot':2.5,'loseAllot':1.4,'mchScore':'2.5:5','gameResult':'2'}
        self.rates={'home_rate':5,'away_rate':4}
    def test_draw_handicap_is_settled(self):
        self.assertEqual(a.calc('handicap',self.r,self.w,self.rates)['outcome'],'away')
    def test_official_grade_and_score_must_match(self):
        self.r['gameResult']='0'
        with self.assertRaisesRegex(ValueError,'product_grade'): a.calc('handicap',self.r,self.w,self.rates)
        self.r['gameResult']='2';self.r['mchScore']='5:5'
        with self.assertRaisesRegex(ValueError,'product_score'): a.calc('handicap',self.r,self.w,self.rates)
    def test_reject_integer_line(self):
        self.r.update(winHandi=-2,loseHandi=2)
        with self.assertRaisesRegex(ValueError,'line'): a.calc('handicap',self.r,self.w,self.rates)
    def test_total_and_prices(self):
        self.r.update(winHandi=9.5,loseHandi=9.5,mchScore='10:0',gameResult='2')
        self.assertEqual(a.calc('total',self.r,self.w,self.rates)['outcome'],'over')
        self.r['winAllot']=1
        with self.assertRaisesRegex(ValueError,'prices'): a.calc('total',self.r,self.w,self.rates)
    def test_drawdown(self):
        rows=[{'model_selection':'home','outcome':out,'prices':{'home':2}} for out in ('home','away','away','home')]
        self.assertEqual(a.metric(rows,'model_selection')['max_drawdown_units'],2)
if __name__=='__main__':unittest.main()
