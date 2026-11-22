import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('basketball',Path(__file__).resolve().parents[1]/'scripts/link_basketball_model_votes.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class BasketballComparison(unittest.TestCase):
    def test_probability_and_selection(self):
        market,vote,mix=m.probabilities([2.0,2.0],['home'])
        self.assertEqual((market,vote,mix),(.5,1.0,.55))
        self.assertEqual(m.choose(mix),'home')
        self.assertIsNone(m.choose(.5))
        self.assertAlmostEqual(m.probabilities([2,4],['away'])[0],2/3)
        self.assertEqual(m.settle('home',100,90,1.7),.7)
        self.assertEqual(m.settle('away',100,90,2),-1)
        self.assertEqual(m.settle('home',100,100,1.7),0)

    def test_deduplicate_fixture_claim_precedence_and_price_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);root=repo/'experiments'/'year';(root/'basketball').mkdir(parents=True);(root/'prices').mkdir()
            raw=b'actual source';digest=hashlib.sha256(raw).hexdigest();(repo/'raw.gz').write_bytes(gzip.compress(raw))
            evidence={'raw_path':'raw.gz','sha256':digest}
            base={'league':'KBL','teams':{'home':'Goyang Sono','away':'Anyang JungKwanJang'},'match_date':'2026-03-05','source':'Miky','source_url':'https://example.test','observed_at':'2026-10-01T00:00:00Z','market':'moneyline','market_raw':'H/A, OT','market_scope':'full_game_including_overtime','side':'home','author':'A','evidence':[evidence]}
            candidates=[dict(base,candidate_id='1',published_at_claim='2026-03-05T00:00:00Z'),dict(base,candidate_id='2'),dict(base,candidate_id='3',author='B',side='away')]
            def lines(path,rows):path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf8')
            lines(root/'basketball/candidates.jsonl',candidates)
            result={'official_id':'game1','match_date':'2026-03-05','home_id':'66','away_id':'70','teams':{'home':'고양 소노 스카이거너스','away':'안양 정관장 레드부스터스'},'home_score':75,'away_score':73,'source_url':'https://api.kbl.or.kr/','evidence':evidence}
            lines(root/'basketball/official-kbl-results.jsonl',[result])
            (root/'official').mkdir();(root/'official/nba-results.json').write_text('[]');(root/'official/nba-date-requests.json').write_text('[]')
            season=repo/'season.jsonl';lines(season,[{'kind':'fixture','league':'KBL','kickoff':'2026-03-05T19:00:00+09:00','payload':{'source_fixture_id':'game1','home_team_id':'66','away_team_id':'70'}}])
            price={'league':'KBL','date':'2026-03-05','market':'full_game_moneyline','home':'고양소노','away':'안양정관','kickoff':'2026-03-05T19:00:00+09:00','odds':[2,1.7],'odds_order':['home','away'],'home_score':75,'away_score':73}
            lines(root/'prices/offers.jsonl',[price]);old=m.SEASON;m.SEASON=season
            try:
                out=repo/'out';summary=m.run(root,out);self.assertEqual(summary['unique_fixtures'],1)
                row=json.loads((out/'comparisons.jsonl').read_text());self.assertEqual(row['category'],'historical_posted_claim');self.assertEqual(len(row['votes']),1);self.assertEqual(row['votes'][0]['candidate_id'],'1')
                lines(root/'prices/offers.jsonl',[price,dict(price,odds=[2.1,1.6])]);self.assertEqual(m.run(root,out)['unique_fixtures'],0)
            finally:m.SEASON=old

    def test_timestamps_require_timezone(self):
        self.assertIsNone(m.timestamp('2025-01-01T12:00:00'))
        self.assertIsNotNone(m.timestamp('2025-01-01T12:00:00Z'))

if __name__=='__main__':unittest.main()
