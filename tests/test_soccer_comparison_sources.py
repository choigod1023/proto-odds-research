import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('collector', Path(__file__).resolve().parents[1] / 'scripts/collect_soccer_comparison_sources.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

class Tests(unittest.TestCase):
    def test_retrospective_and_identity(self):
        row = {'kickoff_kst':'2025-02-15T13:00:00+09:00','price_observed_at':None,'gmTs':250020,'matchSeq':63,'league_id':1,'league_home_name':'포항','league_away_name':'대전','response_sha256':'a'*64,'received_at':'2026-11-03T02:19:41+00:00','fixture_id':'kleague:2025:1:1','home_team_id':'K03','away_team_id':'K10','prices':{'home':1.98,'draw':3.1,'away':3.25},'gmId':'G101','official_score':[0,3]}
        r=c.price_record(row)
        self.assertEqual(r['timing_basis'],'retrospective')
        self.assertEqual(r['payload']['source_fixture_id'],'kleague:2025:1:1')
        self.assertFalse(r['payload']['execution_price_verified'])
        row['price_observed_at']='2025-02-14T00:00:00+00:00'
        with self.assertRaises(ValueError): c.price_record(row)
    def test_reject_naive_kickoff(self):
        with self.assertRaises(ValueError): c.price_record({'kickoff_kst':'2025-02-15T13:00:00','price_observed_at':None})

if __name__ == '__main__': unittest.main()
