import importlib.util
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('retrospective',Path(__file__).with_name('retrospective.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RetrospectiveTests(unittest.TestCase):
    def test_old_and_new_lines_are_not_interchangeable(self):
        self.assertEqual(r.outcome('언더오버', 8.5, 0, 5, 4), 0)
        self.assertEqual(r.outcome('언더오버', 9.5, 0, 5, 4), 1)
        self.assertEqual(r.outcome('핸디캡', -1.5, 0, 101, 100), 0)
        self.assertEqual(r.outcome('핸디캡', -.5, 0, 101, 100), 1)
    def test_only_half_lines(self):
        self.assertIsNone(r.line_of('H -1.0','핸디캡'))
        self.assertIsNone(r.line_of('U 8.25','언더오버'))
        self.assertEqual(r.line_of('H -1.5','핸디캡'),-1.5)
    def test_paired_comparison_uses_each_new_price(self):
        rows=[{'new_product_old_hit':1,'new_product_new_hit':0,'original_ticket_hit':1,'old_odds':[1.8,2.0],'new_odds':[2.2,1.7],'old_side':0,'new_side':1}]
        result=r.aggregate(rows)
        self.assertEqual((result['gain_hits'],result['lost_hits'],result['net_extra_hits']),(0,1,-1))
        self.assertAlmostEqual(result['old_roi'],1.2)
        self.assertAlmostEqual(result['new_roi'],-1)
        self.assertAlmostEqual(result['original_ticket_roi'],.8)
    def test_later_withdrawal_cannot_resurrect_confirmed_score(self):
        terms={'game_no':'3','period':'full_including_extra','unit':'points','market':'total','settlement_rule':'total_half','n_way':2}
        g={'round':132,'date':'11.06(금) 19:00','home':'A','away':'B','sport':'bk','league':'KBL','options':[{'게임번호':'3'}], 'prediction_record':{'market_recommendations':{'items':[{'game_no':'3','terms':terms}]}}}
        h={'history':{'132':{'3':[{'observed_at':'2026-11-06T08:00:00+00:00','market':'언더오버','label':'U 140.5','odds':[1.7,1.8]},{'observed_at':'2026-11-06T09:00:00+00:00','market':'언더오버','label':'U 141.5','odds':[1.8,1.7]}]}}}
        score={'source':'proto','season':2026,'round':132,'game_no':'1','sport':'bk','league':'KBL','home_team':'A','away_team':'B','kickoff':'2026-11-06T19:00:00','home_score':70,'away_score':72,'observed_at':'2026-11-06T12:00:00+00:00','result_state':'confirmed'}
        withdrawn=dict(score,observed_at='2026-11-06T13:00:00+00:00',result_state='unconfirmed')
        args=(h,{'live':[g],'past':[]},{'states':[]})
        self.assertEqual(r.analyze(*args,{'score_rows':[score]})['all_changed']['n'],1)
        self.assertEqual(r.analyze(*args,{'score_rows':[score,withdrawn]})['all_changed']['n'],0)
        other=dict(score,round=131,game_no='9',home_score=71)
        self.assertEqual(r.analyze(*args,{'score_rows':[score,other]})['all_changed']['n'],0)

    def test_naive_score_kickoff_is_kst(self):
        self.assertEqual(r.stamp('2026-11-06T19:00:00'),r.stamp('2026-11-06T10:00:00+00:00'))

if __name__=='__main__':unittest.main()
