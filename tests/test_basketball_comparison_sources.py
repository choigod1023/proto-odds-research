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
        row = {'gmkey':'G1','gameDate':'20260101','gameStart':'1900','tnameH':'H','tnameA':'A','scoreH':88,'scoreA':80,'isEnded':1,'seasonCategoryName':'정규시즌'}
        records = c.kbl_records(meta,json.dumps([row]).encode())
        self.assertEqual([r['kind'] for r in records],['fixture','result'])
        self.assertNotIn('scoreH', records[0]['payload'])
        self.assertEqual(records[1]['payload']['home_score'],88)
        row['isEnded']=0
        self.assertEqual(len(c.kbl_records(meta,json.dumps([row]).encode())),1)
        self.assertEqual(records[0]['kickoff'],'2026-01-01T19:00:00+09:00')
        self.assertEqual(records[0]['timestamp_basis'],'unknown')
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
            self.assertEqual(one['timestamp_basis'],'http_response_body_completed')
            self.assertEqual(calls,[15])
            (p/'a.body.gz').write_bytes(gzip.compress(b'tampered'))
            with self.assertRaises(ValueError): c.capture('a','https://example.test',p,opener)
    def test_missing_official_identity_is_not_joined(self):
        meta={'status':200,'url':'u','body_sha256':'x','received_at':'2026-01-01T00:00:00+00:00'}
        self.assertEqual(c.kbl_records(meta,b'[{"gameDate":"20260101"}]'),[])


class SeasonTests(unittest.TestCase):
    def test_year_ranges_cover_leap_year_without_gaps(self):
        spans=list(c.annual_ranges(dt.date(2024,12,31),dt.date(2026,1,1)))
        self.assertEqual(spans,[(dt.date(2024,12,31),dt.date(2024,12,31)),(dt.date(2025,1,1),dt.date(2025,12,31)),(dt.date(2026,1,1),dt.date(2026,1,1))])

    def test_injury_join_requires_unique_exact_pair_date_time(self):
        f={'record_id':'nba:fixture:123','sport':'bk','league':'NBA','kickoff':'2026-11-05T00:00:00+00:00','home':'Washington Wizards','away':'Atlanta Hawks','payload':{'source_fixture_id':'123','home_tricode':'WAS','away_tricode':'ATL','game_time_eastern_claim':'2026-11-04T19:00:00Z'}}
        meta={'url':'https://example.test','body_sha256':'a'*64,'received_at':'2026-11-04T05:00:00+00:00'}
        text='Injury Report: 11/04/26 12:15 AM\n11/04/2026\n07:00 (ET)\nATL@WAS\nAtlanta Hawks\nSmith, Jane\nOut\n'
        rows=c.injury_records(meta,text,[f]);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['payload']['players'][0]['status'],'Out')
        self.assertFalse(rows[0]['payload']['confirmed_lineup'])
        self.assertEqual(c.injury_records(meta,text,[f,f]),[])
        self.assertEqual(c.injury_records(meta,text.replace('07:00','08:00'),[f]),[])

    def test_kbl_other_competitions_excluded(self):
        meta={'status':200,'url':'u','body_sha256':'x','received_at':'2026-01-01T00:00:00+00:00'}
        row={'gmkey':'G1','gameDate':'20260101','gameStart':'1900','tnameH':'H','tnameA':'A','seasonCategoryName':'EASL'}
        self.assertEqual(c.kbl_records(meta,json.dumps([row]).encode()),[])

    def test_nba_feed_rejects_wrong_date_or_unresolved_team(self):
        meta={'status':200,'url':'https://www.nba.com/games?date=2025-01-01','body_sha256':'a'*64,'received_at':'2026-01-01T00:00:00+00:00'}
        props={'selectedDate':'2025-01-02','gameCardFeed':{'modules':[]}}
        html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':props}})+'</script>'
        with self.assertRaises(ValueError): c.nba_game_page(meta,html.encode())

    def test_cumulative_budget_and_resume(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);e=p/'evidence';e.mkdir()
            for i in range(20): (e/(str(i)+'.json')).write_text(json.dumps({'url':'https://example.test/'+str(i)}),encoding='utf8')
            args=Namespace(start='2025-01-01',end='2025-01-01',nba_cursor=None,nba_max_days=1,refresh=False,capture_id=None,max_new_requests=4,total_http_budget=60,evidence=e,nba_only=True,injury_pdf=None,output=p/'out.jsonl')
            c.season_run(args)
            manifest=json.loads((p/'out.manifest.json').read_text(encoding='utf8'))
            self.assertEqual(manifest['nba_next_cursor'],'2025-01-01')
            self.assertEqual(manifest['missing'][0]['reason'],'budget')
            self.assertEqual(manifest['new_url_attempts'],0)

    def test_mutable_capture_versions_preserve_old_snapshot_and_resume(self):
        from argparse import Namespace
        from unittest.mock import patch
        today=dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
        props={'selectedDate':today,'gameCardFeed':{'modules':[],'nextPageToken':None}}
        body=('<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':props}})+'</script>').encode()
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return body
            def geturl(self): return 'https://www.nba.com/games?date='+today
        class Opener:
            def open(self,request,timeout): return Response()
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.urllib.request,'build_opener',return_value=Opener()):
            p=Path(tmp);e=p/'e'
            args=Namespace(start=today,end=today,nba_cursor=None,nba_max_days=1,refresh=True,capture_id='v1',max_new_requests=4,total_http_budget=60,evidence=e,nba_only=True,injury_pdf=None,output=p/'out.jsonl')
            c.season_run(args)
            old=(e/('nba-games-'+today.replace('-','')+'-v1.json')).read_bytes()
            with self.assertRaises(ValueError): c.season_run(args)
            args.refresh=False
            c.season_run(args)
            args.capture_id='v2';args.refresh=True
            c.season_run(args)
            self.assertEqual(old,(e/('nba-games-'+today.replace('-','')+'-v1.json')).read_bytes())
            self.assertTrue((e/('nba-games-'+today.replace('-','')+'-v2.json')).exists())

if __name__ == '__main__': unittest.main()
