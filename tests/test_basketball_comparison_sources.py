import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('collector', Path(__file__).resolve().parents[1] / 'scripts/collect_basketball_comparison_sources.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class Tests(unittest.TestCase):
    def test_kbl_result_separate_and_future_not_final(self):
        meta = {'status':200,'url':'https://example.test','body_sha256':'x','received_at':'2026-01-01T00:00:00+00:00'}
        row = {'gmkey':'G1','gameDate':'20260101','gameStart':'1900','tnameH':'H','tnameA':'A','scoreH':88,'scoreA':80,'isEnded':1}
        records = c.kbl_records(meta,json.dumps([row]).encode())
        self.assertEqual([r['kind'] for r in records],['fixture','result'])
        self.assertNotIn('scoreH', records[0]['payload'])
        self.assertEqual(records[1]['payload']['home_score'],88)
        row['isEnded']=0
        self.assertEqual(len(c.kbl_records(meta,json.dumps([row]).encode())),1)
        self.assertEqual(records[0]['kickoff'],'2026-01-01T19:00:00+09:00')
    def test_cache_preserves_receipt_and_verifies_hash(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'body'
            def geturl(self): return 'https://example.test'
        calls=[]
        def opener(req,timeout): calls.append(timeout); return Response()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            one,_=c.capture('a','https://example.test',p,opener)
            two,_=c.capture('a','https://example.test',p,opener)
            self.assertEqual(one,two)
            self.assertEqual(calls,[15])
            (p/'a.body.gz').write_bytes(gzip.compress(b'tampered'))
            with self.assertRaises(ValueError): c.capture('a','https://example.test',p,opener)
    def test_missing_official_identity_is_not_joined(self):
        meta={'status':200,'url':'u','body_sha256':'x','received_at':'2026-01-01T00:00:00+00:00'}
        self.assertEqual(c.kbl_records(meta,b'[{"gameDate":"20260101"}]'),[])

if __name__ == '__main__': unittest.main()
