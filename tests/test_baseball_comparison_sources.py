import gzip, importlib.util, json, tempfile, unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'scripts/collect_baseball_comparison_sources.py'
s=importlib.util.spec_from_file_location('collector',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Resp:
 status_code=200;content=b'{"hello":1}'
class Session:
 calls=0
 def post(self,*args,**kwargs):
  self.calls+=1;assert kwargs['timeout']==(5,15);return Resp()
class Tests(unittest.TestCase):
 def test_cache_keeps_real_receipt_and_detects_corruption(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);s=Session();raw,r=m.fetch(p,'one','https://example.org',{'a':'b'},s);raw2,r2=m.fetch(p,'one','https://example.org',{'a':'b'},s)
   self.assertEqual((raw,r),(raw2,r2));self.assertEqual(s.calls,1)
   (p/r['body_file']).write_bytes(gzip.compress(b'changed'))
   with self.assertRaisesRegex(ValueError,'hash mismatch'):m.fetch(p,'one','https://example.org',{'a':'b'},s)
 def test_fixture_timing_and_unconfirmed(self):
  g={'G_DT':'20261104','G_TM':'18:30','G_ID':'20261104SSKT0','HOME_NM':'KT','AWAY_NM':'삼성'}
  meta={'received_at':'2026-11-04T05:00:00+00:00','url':'https://example.org','body_sha256':'f'*64}
  doc=json.dumps({'code':'100','game':[g]}).encode(); lineup=json.dumps([[{'LINEUP_CK':False}],[],[],[],[]]).encode()
  rows=m.records(doc,meta,lineup,meta)
  self.assertEqual(rows[1]['payload']['status'],'not_confirmed');self.assertTrue(rows[1]['payload']['pregame_receipt']);self.assertIsNone(rows[1]['payload']['ordered_nine'])
  meta['received_at']='2026-11-04T10:00:00+00:00';self.assertFalse(m.records(doc,meta,lineup,meta)[1]['payload']['pregame_receipt'])
 def test_identity_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);m.fetch(p,'one','https://example.org',{'a':'b'},Session())
   with self.assertRaisesRegex(ValueError,'request mismatch'):m.fetch(p,'one','https://example.org',{'a':'c'},Session())
if __name__=='__main__':unittest.main()
