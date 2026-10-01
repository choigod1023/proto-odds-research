from datetime import date,timedelta
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from shot_quality_evaluation import model,evaluate


def sample():
    return [{'day':str(date(2015,8,1)+timedelta(days=i//20)),'match_id':i//20,
             'penalty':False,'score_diff':i%3-1,'man_diff':0,'minute':i%90,'home':i%2,
             'goal':int(i%9==0),'xg':.08+.02*(i%4)} for i in range(200)]


class QualityTests(unittest.TestCase):
    def test_test_goal_not_feature(self):
        rows=sample();train,test=rows[:140],rows[140:]
        before=model(train,test,True)
        after=model(train,[{**r,'goal':1-r['goal']} for r in test],True)
        np.testing.assert_array_equal(before,after)
        self.assertTrue(np.all((before>0)&(before<1)))

    def test_complete_dates_split_and_no_pregame_claim(self):
        report,data=evaluate(sample())
        self.assertFalse(report['production_allowed'])
        self.assertEqual(report['train_games'],7)
        self.assertEqual(report['test_games'],3)
        self.assertTrue(all(r['day']>=report['split_date'] for r in data['test_shots']))
        self.assertIn('not pregame',report['warning'])

    def test_no_dates_is_not_success(self):
        report,_=evaluate([])
        self.assertEqual(report['status'],'insufficient_dates')


if __name__=='__main__':unittest.main()
