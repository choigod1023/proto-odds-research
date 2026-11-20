"""Offline regressions: full vectors, boundary dates, SEQ and host-stop behavior."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('year_proto', ROOT/'scripts/research_collect_year_proto.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CollectorTests(unittest.TestCase):
    def test_normalization_regressions(self):
        m.self_test()

    def test_three_way_draw_and_missing_price(self):
        g=m.GameRow(2026,30,'007','03.15(일) 16:30','sc','K리그1','hm','',
                    '홈 1','1 원정','',[2.0,3.0,4.0],'무승부',False)
        o,why=m.normalize(g,{'source_url':'synthetic'})
        self.assertIsNone(why)
        self.assertEqual(o['odds_order'],['home','draw','away'])
        self.assertEqual(o['winner'],1)
        self.assertFalse(o['official_result_verified'])
        self.assertEqual(m.normalize(replace(g,odds=[2.0,4.0]),{})[1], 'incomplete_or_invalid_vector')

    def test_conflicting_seq_is_rejected(self):
        html="get_gameinfo_body('proto','pt1','2026','1','','','123')"+"get_gameinfo_body('proto','pt1','2026','2','','','456')"
        with self.assertRaises(ValueError):
            m.sequence(html,2026,1)

    def test_403_is_cached_and_blocks_all_subsequent_gets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'raw').mkdir()
            c=m.Collector(out)
            response=Mock(status_code=403,content=b'Forbidden',url=m.BASE+'/index.htm')
            c.session.get=Mock(return_value=response)
            with patch.object(m.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'HTTP403'):
                    c.get('index',2026,1,{})
                with self.assertRaisesRegex(RuntimeError,'persistent HTTP403'):
                    c.get('index',2026,2,{})
            self.assertEqual(c.session.get.call_count,1)
            self.assertEqual(len(m.read_lines(out/'requests.jsonl')),1)
            self.assertTrue((out/'raw/2026-001-index.bin').exists())

    def test_redirect_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'raw').mkdir()
            c=m.Collector(out)
            c.session.get=Mock(return_value=Mock(status_code=302,content=b'redirect',url=m.BASE+'/index.htm'))
            with patch.object(m.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'HTTP302'):
                    c.get('index',2026,1,{})
            self.assertFalse(c.session.get.call_args.kwargs['allow_redirects'])
            self.assertEqual(c.session.headers['X-Requested-With'],'XMLHttpRequest')

    def test_request_spacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp); (out/'raw').mkdir()
            c=m.Collector(out); c.last=100.0
            c.session.get=Mock(return_value=Mock(status_code=200,content=b'ok',url=m.BASE+'/index.htm'))
            with patch.object(m.time,'monotonic',return_value=101.0), patch.object(m.time,'sleep') as sleeper:
                c.get('index',2026,1,{})
            sleeper.assert_called_once_with(1.5)


if __name__=='__main__':
    unittest.main()
