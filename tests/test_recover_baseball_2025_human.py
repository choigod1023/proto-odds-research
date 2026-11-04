import contextlib,gzip,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from scripts import recover_baseball_2025_human as human

class HumanRecoveryTests(unittest.TestCase):
    def test_parser_keeps_article_scope_and_separate_choices(self):
        parser=human.ArticleParser()
        parser.feed('<div class="tt_article_useless_p_margin"><p>결론: LG 승, 8.5 오버<br/>끝</p></div><p>outside</p>')
        result=' '.join(parser.parts)
        self.assertIn('결론: LG 승, 8.5 오버',result)
        self.assertNotIn('outside',result)

    def test_403_stops_following_http(self):
        def forbidden(req,timeout):raise HTTPError(req.full_url,403,'forbidden',{},io.BytesIO(b'blocked'))
        with tempfile.TemporaryDirectory() as d, patch.object(human,'OUT',Path(d)), patch.object(human,'urlopen',side_effect=forbidden) as http, contextlib.redirect_stdout(io.StringIO()):
            human.run()
            self.assertEqual(http.call_count,1)
            receipt=json.loads((Path(d)/'allstv24.receipt.json').read_text(encoding='utf8'))
            self.assertEqual(receipt['http_status'],403)
            self.assertEqual(receipt['body_sha256'],human.sha(b'blocked'))
            self.assertEqual(gzip.decompress((Path(d)/'allstv24.body.gz').read_bytes()),b'blocked')
            human.run()
            self.assertEqual(http.call_count,1)

    def test_retrospective_one_row_and_pregame_limits(self):
        root=Path('experiments/matched-baseball-20261104/recovery-2025/human')
        result=json.loads((root/'joined-2025.json').read_text(encoding='utf8'));r=result['rows'][0]
        self.assertEqual(result['summary']['matched_games'],1)
        self.assertEqual(result['summary']['strict_games'],0)
        self.assertEqual((r['fixture_id'],r['official_game_id']),('20250719LTLG02025','20250719LTLG0'))
        self.assertEqual((r['away_score'],r['home_score']),(6,1))
        self.assertEqual(r['source_win_pick_excerpt'],'LG 승')
        self.assertEqual(r['separate_total_pick_excluded'],'8.5 오버')
        self.assertFalse(r['strict_eligible'])
        self.assertTrue(all(v=='home' for v in r['selections'].values()))
        self.assertTrue(all(v==-1 for v in r['profit_units'].values()))
        self.assertEqual(human.sha(gzip.decompress((root/'kboinsight.body.gz').read_bytes())),r['source_body_sha256'])
        self.assertEqual(human.sha(gzip.decompress((root/'official-20250719.body.gz').read_bytes())),r['official_schedule_sha256'])

if __name__=='__main__':unittest.main()
