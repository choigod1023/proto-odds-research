import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from external_feature_validation import lagged_stats, prepare, EXTERNAL, STAT_PAIRS
from sport_hybrid_validation import fit, predict


class ExternalFeatureTests(unittest.TestCase):
    def matches(self):
        rows=[]
        for i in range(6):
            row=dict(Date=f'{1+i*3:02d}/01/2024',HomeTeam='A',AwayTeam='B',
                     FTHG=1,FTAG=0,FTR='H',league='EPL',season=2023,
                     B365H=2.,B365D=3.,B365A=4.)
            for h,a in STAT_PAIRS.values(): row.update({h:float(i+4),a:2.})
            rows.append(row)
        return prepare(pd.DataFrame(rows))[0]

    def test_current_and_future_statistics_cannot_change_prediction_features(self):
        d=self.matches()
        before=lagged_stats(d)
        for pair in STAT_PAIRS.values():
            d.loc[3:,list(pair)]=999.
        after=lagged_stats(d)
        np.testing.assert_allclose(before.loc[:3,EXTERNAL],after.loc[:3,EXTERNAL],equal_nan=True)

    def test_prior_three_games_used(self):
        f=lagged_stats(self.matches())
        self.assertTrue(f.loc[:2,EXTERNAL].isna().all().all())
        # A's prior net shots = 2,3,4; B = -2,-3,-4.
        self.assertEqual(f.loc[3,'shots_diff'],6.)

    def test_one_day_gap_not_used(self):
        d=self.matches()
        d.loc[3,['date','kickoff']]=pd.Timestamp('2024-01-08')
        f=lagged_stats(d)
        self.assertTrue(f.loc[3,EXTERNAL].isna().all())

    def test_duplicates_and_score_conflicts_excluded(self):
        raw=pd.DataFrame([
            dict(Date='01/01/2024',HomeTeam='A',AwayTeam='B',FTHG=1,FTAG=0,FTR='H',league='EPL'),
            dict(Date='01/01/2024',HomeTeam='A',AwayTeam='B',FTHG=1,FTAG=0,FTR='H',league='EPL'),
            dict(Date='02/01/2024',HomeTeam='C',AwayTeam='D',FTHG=1,FTAG=0,FTR='A',league='EPL'),
        ])
        for pair in STAT_PAIRS.values():
            for c in pair: raw[c]=0
        d,q=prepare(raw)
        self.assertTrue(d.empty)
        self.assertEqual(q['duplicate_rows_excluded'],2)
        self.assertEqual(q['score_conflicts'],1)

    def test_test_labels_unused_by_fitted_model(self):
        d=pd.DataFrame(dict(market=[[.4,.3,.3]]*60,y=np.arange(60)%3,x=np.arange(60)))
        model=fit(d,['x'],True)
        before=predict(model,d)
        d.y=2
        np.testing.assert_allclose(before,predict(model,d))
        np.testing.assert_allclose(before.sum(1),1)
