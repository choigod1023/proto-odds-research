import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from calibration_transfer import candidates


class TransferTests(unittest.TestCase):
    def frame(self, start, n):
        return pd.DataFrame(dict(date=pd.date_range(start, periods=n), market=[[.4,.3,.3]]*n,
                                 league=['test']*n, y=np.arange(n)%3))

    def test_overlap_rejected(self):
        d=self.frame('2025-01-01', 30)
        with self.assertRaises(ValueError): candidates(d,d)

    def test_future_labels_unused_and_sparse_league_fallback(self):
        train=self.frame('2023-01-01', 100)
        test=self.frame('2025-01-01', 10)
        before=candidates(train,test)
        test.y=2
        after=candidates(train,test)
        for name,p in before.items():
            np.testing.assert_allclose(p,after[name])
            np.testing.assert_allclose(p.sum(1),1)
        np.testing.assert_allclose(before['league'],before['pooled'])
