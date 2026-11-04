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


class SeasonTests(unittest.TestCase):
    def test_budget_and_cache_identity(self):
        import tempfile, json, gzip
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); raw=b'{"ok":true}'; params={'year':'2025'}
            (root/'one.body.gz').write_bytes(gzip.compress(raw))
            (root/'one.json').write_text(json.dumps({'url':'https://example.test','params':params,'body_sha256':c.sha(raw)}),encoding='utf8')
            b=c.Budget(0)
            with patch('requests.request',side_effect=AssertionError('unexpected network')):
                self.assertEqual(c.cached_request(root,'one','https://example.test','GET',params,b)[0],raw)
                self.assertEqual(b.used,0)
                with self.assertRaises(ValueError): c.cached_request(root,'one','https://example.test','GET',{'year':'2026'},b)
                with self.assertRaises(RuntimeError): c.cached_request(root,'missing','https://example.test','GET',params,b)
            (root/'one.body.gz').write_bytes(gzip.compress(b'tampered'))
            with self.assertRaises(ValueError): c.cached_request(root,'one','https://example.test','GET',params,c.Budget(0))
        with self.assertRaises(ValueError): c.Budget(61)

    def test_official_page_identity_and_empty_lineup(self):
        html='''<select id="year"><option selected value="2026"></option></select><select id="meetSeq"><option selected value="1"></option></select><select id="gameId"><option selected value="205"></option></select><div class="game-info"><div class="team K01"></div><div class="team K02"></div></div><div class="team-versus"><div class="versus"><p>2026/11/07 14:00</p></div></div>'''
        fixture={'year':2026,'leagueId':1,'gameId':205,'homeTeam':'K01','awayTeam':'K02','gameDate':'2026.11.07'}
        self.assertEqual(c.parse_official_page(html.encode(),fixture),('not_displayed_on_official_page',[]))
        fixture['homeTeam']='K03'
        with self.assertRaises(ValueError): c.parse_official_page(html.encode(),fixture)

if __name__ == '__main__': unittest.main()
