import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('new_replay', ROOT/'scripts/compare_new_tipsters.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NewTipsterTests(unittest.TestCase):
    def test_current_and_future_results_do_not_change_current_features(self):
        import pandas as pd
        from features import build_features
        from pi_ratings import run_pi
        rows=[dict(date=pd.Timestamp('2025-01-01')+pd.Timedelta(days=i),
                   year=2025,league='NBA',sport='bk',home_team='a',away_team='b',
                   home_score=100,away_score=90,outcome=1.) for i in range(12)]
        original=pd.DataFrame(rows)
        changed=original.copy()
        changed.loc[8:,['home_score','away_score','outcome']]=[0,200,0.]
        for builder in (build_features,run_pi):
            a,b=builder(original),builder(changed)
            columns=[c for c in a.columns if c!='outcome']
            pd.testing.assert_frame_equal(a.loc[:8,columns],b.loc[:8,columns])

    def test_weight_and_pick_switch(self):
        self.assertEqual(m.decisions([.51,.49],1)['research90_tipster10'],1)
        self.assertEqual(m.decisions([.8,.2],1)['research90_tipster10'],0)

    def test_invalid_probability(self):
        for p,t in [([float('nan'),.5],0),([.7,.7],0),([.5,.5],2)]:
            with self.assertRaises(ValueError): m.decisions(p,t)

    def test_duplicate_and_old_rejected(self):
        r=dict(sport='bk',date='2025-01-01',home='a',away='b')
        with self.assertRaises(ValueError): m.validate_new([r,r],[])
        with self.assertRaises(ValueError): m.validate_new([r],[r])

    def test_monthly_exposure_cap(self):
        rows=[dict(sport='bk',date='2025-01-01',home=str(i),away='b',odds=[2,2],winner=1,decisions={'x':0}) for i in range(9)]
        r=m.capped(rows,'x')
        self.assertEqual(r['n'],5)
        self.assertEqual(r['profit_krw'],-50000)

    def test_actual_artifact_counts_and_returns(self):
        r=json.loads((ROOT/'experiments/new-tipsters-20261026/results.json').read_text(encoding='utf8'))
        self.assertEqual(r['joined_new'],12)
        self.assertEqual(r['strict_pregame_eligible'],0)
        self.assertEqual(len({m.key(x) for x in r['ledger']}),12)
        for model in ('research_baseline','tipster','research90_tipster10'):
            self.assertEqual(m.score(r['ledger'],model)['profit_krw'],-36600)
            self.assertEqual(m.score(r['ledger'],model)['hits'],6)
        for row in r['ledger']:
            self.assertTrue(row['official'].startswith('https://'))
            self.assertTrue(row['price_rows'])
            h,a=row['score']
            expected=0 if h>a else (1 if h==a or row['sport']!='sc' else 2)
            self.assertEqual(expected,row['winner'])


if __name__=='__main__': unittest.main()
