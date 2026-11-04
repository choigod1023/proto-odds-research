import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('proof', Path(__file__).resolve().parents[1] / 'scripts/find_pregame_archive_proofs.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ArchiveProofTests(unittest.TestCase):
    def test_capture_must_precede_cutoff(self):
        data = b'[["timestamp","original","statuscode","digest","mimetype"],["20251029235959","https://example.com/","200","ABC","text/html"],["20251030000000","https://example.com/","200","DEF","text/html"]]'
        self.assertEqual([x['digest'] for x in m.parse_cdx(data, '20251030000000')], ['ABC'])
        self.assertFalse(m.classify('20251030000000', '20251030000000', True))
        self.assertFalse(m.classify('20251029235959', '20251030000000', False))

    def test_invalid_cdx_is_not_evidence(self):
        for value in (b'[]', b'bad', b'{}'):
            self.assertEqual(m.parse_cdx(value, '20251030000000'), [])

    def test_article_capture_does_not_itself_verify_content(self):
        self.assertFalse(m.classify('20251029010000', '20251030000000', False))

if __name__ == '__main__': unittest.main()
