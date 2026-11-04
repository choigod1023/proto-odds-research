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
  self.assertEqual(rows[0]['payload']['status'],'not_confirmed');self.assertTrue(rows[0]['payload']['pregame_receipt']);self.assertIsNone(rows[0]['payload']['ordered_nine'])
  meta['received_at']='2026-11-04T10:00:00+00:00';self.assertFalse(m.records(doc,meta,lineup,meta)[0]['payload']['pregame_receipt'])
 def test_identity_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);m.fetch(p,'one','https://example.org',{'a':'b'},Session())
   with self.assertRaisesRegex(ValueError,'request mismatch'):m.fetch(p,'one','https://example.org',{'a':'c'},Session())
class BatchTests(unittest.TestCase):
 def test_multi_game_resume_and_refresh(self):
  from datetime import datetime, timedelta
  day=(datetime.now(m.probe.KST)+timedelta(days=1)).date(); ds=day.strftime('%Y%m%d')
  class Response:
   status_code=200
   def __init__(self,body):self.content=json.dumps(body).encode()
  class Fake:
   def __init__(self):self.calls=[]
   def post(self,url,data,**kw):
    self.calls.append((url,data))
    if url==m.probe.SCHEDULE:
     games=[{'G_DT':ds,'G_TM':'18:30','G_ID':ds+suffix,'HOME_NM':'H','AWAY_NM':'A','LE_ID':'1','SR_ID':'0','SEASON_ID':str(day.year)} for suffix in ['AA','BB']]
     return Response({'code':'100','game':games})
    return Response([[{'LINEUP_CK':False}],[],[],[],[]])
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); f=Fake(); old=m.time.sleep;m.time.sleep=lambda _:None
   try:
    args=(day,day,2,root/'e',root/'out.jsonl',root/'cursor.json')
    first=m.run(*args,capture_id='capture1',session=f);self.assertEqual(first['status'],'partial');self.assertEqual(first['new_requests'],2)
    second=m.run(*args,capture_id='capture1',session=f);self.assertEqual(second['status'],'complete');self.assertEqual(second['new_requests'],1)
    oldrows=[json.loads(z) for z in (root/'out.jsonl').read_text(encoding='utf8').splitlines()];self.assertEqual(len(oldrows),4)
    third=m.run(day,day,3,root/'e',root/'out.jsonl',root/'cursor.json',capture_id='capture2',refresh=True,session=f);self.assertEqual(third['new_requests'],3)
    newrows=[json.loads(z) for z in (root/'out.jsonl').read_text(encoding='utf8').splitlines()];self.assertEqual(len(newrows),8);self.assertEqual(newrows[:4],oldrows)
   finally:m.time.sleep=old
 def test_failure_is_recorded_and_resume_retries(self):
  from datetime import date
  class Failed:
   calls=0
   def post(self,*args,**kwargs):
    self.calls+=1
    if self.calls==1: raise m.requests.Timeout('timeout')
    return type('R',(),{'status_code':200,'content':b'{"code":"100","game":[]}'})()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);f=Failed();old=m.time.sleep;m.time.sleep=lambda _:None
   try:
    args=(date.today(),date.today(),1,root/'e',root/'out',root/'cursor')
    self.assertEqual(m.run(*args,capture_id='retry',session=f)['status'],'error')
    result=m.run(*args,capture_id='retry',session=f)
    self.assertEqual(result['status'],'complete');self.assertEqual(result['empty_days'],1);self.assertEqual(f.calls,2)
    self.assertEqual(len(list((root/'e').glob('*-error-*.json'))),1)
   finally:m.time.sleep=old
 def test_bounds(self):
  from datetime import date
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):m.run(date.today(),date.today(),61,Path(d)/'e',Path(d)/'o',Path(d)/'c')
if __name__=='__main__':unittest.main()
