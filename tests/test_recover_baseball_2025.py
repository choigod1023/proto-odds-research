import contextlib
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import recover_baseball_2025 as recovery

class Recovery2025Tests(unittest.TestCase):
    def test_restriction_stops_without_second_request(self):
        with tempfile.TemporaryDirectory() as d, patch.object(recovery,'OUT',Path(d)), \
             patch.object(recovery,'fetch_one',return_value={'http_status':403}) as fetch, \
             patch.object(recovery,'audit_official_schedule',return_value={'official_games':0,'completed_with_exact_id_price_and_both_models':0,'full_joined_publisher_games':0}), \
             contextlib.redirect_stdout(io.StringIO()):
            recovery.run()
            self.assertEqual(fetch.call_count,1)
            summary=json.loads((Path(d)/'retrieval-summary.json').read_text(encoding='utf8'))
            self.assertEqual(summary['not_attempted'],4)
            self.assertTrue(summary['stopped_on_restriction'])

    def test_fake_http_429_stop_and_receipt(self):
        def restricted(request, timeout):
            raise HTTPError(request.full_url,429,'rate limited',{},io.BytesIO(b'restricted'))
        with tempfile.TemporaryDirectory() as d, patch.object(recovery,'OUT',Path(d)), \
             patch.object(recovery,'urlopen',side_effect=restricted) as transport, \
             patch.object(recovery,'audit_official_schedule',return_value={'official_games':0,'completed_with_exact_id_price_and_both_models':0,'full_joined_publisher_games':0}), \
             contextlib.redirect_stdout(io.StringIO()):
            recovery.run()
            self.assertEqual(transport.call_count,1)
            receipt=json.loads((Path(d)/'178033937.receipt.json').read_text(encoding='utf8'))
            self.assertEqual(receipt['http_status'],429)
            self.assertEqual(receipt['timestamp_basis'],'http_response_body_completed')
            self.assertEqual(receipt['body_sha256'],recovery.digest(b'restricted'))
            recovery.run()
            self.assertEqual(transport.call_count,1)

    def test_fake_http_all_five_then_cache(self):
        class FakeResponse:
            status=200
            headers={'Content-Type':'text/html'}
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self): return b'<html>example</html>'
            def geturl(self): return 'https://example.invalid/'
        with tempfile.TemporaryDirectory() as d, patch.object(recovery,'OUT',Path(d)), \
             patch.object(recovery,'urlopen',return_value=FakeResponse()) as transport, \
             patch.object(recovery,'audit_official_schedule',return_value={'official_games':0,'completed_with_exact_id_price_and_both_models':0,'full_joined_publisher_games':0}), \
             contextlib.redirect_stdout(io.StringIO()):
            recovery.run()
            self.assertEqual(transport.call_count,5)
            recovery.run()
            self.assertEqual(transport.call_count,5)

    def test_cache_verifies_body_and_does_not_request(self):
        with tempfile.TemporaryDirectory() as d, patch.object(recovery,'OUT',Path(d)), patch.object(recovery,'urlopen',side_effect=AssertionError('unexpected HTTP')):
            page,slug=recovery.PAGES[0];body=b'cached error'
            raw=Path(d)/f'{page}.body.gz';meta=Path(d)/f'{page}.receipt.json'
            raw.write_bytes(gzip.compress(body,mtime=0))
            receipt={'url':recovery.url_for(page,slug),'body_sha256':recovery.digest(body),'http_status':404,'body_completed_at':'2026-11-04T00:00:00+00:00'}
            meta.write_text(json.dumps(receipt),encoding='utf8')
            self.assertEqual(recovery.fetch_one(page,slug),receipt)
            raw.write_bytes(gzip.compress(b'tampered',mtime=0))
            with self.assertRaises(ValueError): recovery.fetch_one(page,slug)

    def test_real_archive_explicit_blockers_and_official_games(self):
        root=Path('experiments/matched-baseball-20261104/recovery-2025')
        audit=json.loads((root/'official-2025-audit.json').read_text(encoding='utf8'))
        self.assertEqual(audit['publisher_page_official_identity_proven'],0)
        self.assertEqual(audit['full_joined_publisher_games'],0)
        self.assertEqual(audit['official_games'],5)
        self.assertEqual(len(audit['partial_model_price_result_rows']),2)
        self.assertEqual(sum(x['official_game_state']=='4' for x in audit['schedule']),3)
        self.assertEqual(recovery.digest(gzip.decompress((root/'official-schedule.body.gz').read_bytes())),audit['official_schedule_source_sha256'])
        for page,slug in recovery.PAGES:
            receipt=json.loads((root/f'{page}.receipt.json').read_text(encoding='utf8'))
            self.assertEqual(receipt['http_status'],404)
            self.assertEqual(receipt['url'],recovery.url_for(page,slug))
            self.assertEqual(recovery.digest(gzip.decompress((root/f'{page}.body.gz').read_bytes())),receipt['body_sha256'])

if __name__=='__main__': unittest.main()
