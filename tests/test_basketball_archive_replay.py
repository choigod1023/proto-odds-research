import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from basketball_archive_replay import fit, replay, probability

def row(year, day, h, a, kind='total', line='200.5', score=(100,100), outcome='under'):
    key=('NBA',datetime(year,1,day,tzinfo=timezone.utc),'H','A',h,a)
    m=SimpleNamespace(kind=kind,line=line)
    return ('1','hash',key,m,{'under':1.9,'over':1.9},outcome,score)

class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.train=[row(2025,1,'a','b',score=(90,100)),row(2025,2,'b','a',score=(120,100))]
        self.test=row(2026,1,'a','b',score=(90,100))
    def test_fitting_excludes_all_2026_scores(self):
        altered=row(2026,1,'a','b',score=(400,2),outcome='over')
        self.assertEqual(fit(self.train+[self.test]),fit(self.train+[altered]))
        self.assertEqual(replay(self.train+[self.test])['training'],replay(self.train+[altered])['training'])
    def test_duplicate_quote_rejected_even_if_line_differs(self):
        duplicate=row(2026,1,'a','b',line='201.5',score=(90,100))
        g=replay(self.train+[self.test,duplicate])['groups']['NBA/total']
        self.assertEqual(g['counts']['ambiguous_quotes'],1)
        self.assertNotIn('market',g['arms'])
    def test_evaluation_all_arms_share_sample_and_invalid_line_rejected(self):
        invalid=row(2026,2,'a','b',line='200',score=(100,110),outcome='over')
        g=replay(self.train+[self.test,invalid])['groups']['NBA/total']
        self.assertEqual(g['counts']['invalid_line_or_price'],1)
        self.assertEqual({v['n'] for v in g['arms'].values()},{1})
        self.assertEqual(g['arms']['market']['brier'],.25)
        self.assertAlmostEqual(g['arms']['market']['roi'], .9)
    def test_handicap_home_probability_follows_line_direction(self):
        model=fit(self.train)['NBA']
        key=('NBA',datetime(2026,1,1,tzinfo=timezone.utc),'a','b')
        hard=probability(model,key,SimpleNamespace(kind='handicap',line='-5.5'),'static')
        easy=probability(model,key,SimpleNamespace(kind='handicap',line='5.5'),'static')
        self.assertLess(hard,easy)
        self.assertAlmostEqual(probability({'means':[200,0], 'sd':[10,10], 'teams':{}},key,SimpleNamespace(kind='handicap',line='0.5'),'static'),0.5199388058,places=8)

    def test_conflicting_training_score_excluded(self):
        conflict=row(2025,1,'a','b',score=(20,180))
        self.assertEqual(fit(self.train+[conflict]),{})

if __name__=='__main__': unittest.main()
