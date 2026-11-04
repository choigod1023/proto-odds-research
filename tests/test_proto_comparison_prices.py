import gzip
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'scripts/collect_proto_comparison_prices.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class CollectorTest(unittest.TestCase):
    def fixture(self):
        row = dict(itemCode='SC', leagueCode='SC001', betId='1', betTypId='1', betNm='축구 승무패', handi=0, gameSubject='', winHandi=0, drawHandi=0, loseHandi=0, gameReject='0', buyReject='0', gameDate=1794000000000, winAllot=2, drawAllot=3, loseAllot=4, homeName='A', awayName='B', matchSeq=1, protoStatus='0')
        return {'gmTs':'260130', 'currentLottery':{'gmId':'G101','gmTs':260130,'saleProgress':True,'saleStartDate':1793000000000,'saleEndDate':1793900000000}, 'compSchedules':{'keys':list(row), 'datas':[list(row.values())]}}
    def meta(self):
        return {'url':c.DETAIL,'received_at':'2026-11-04T00:00:00+00:00','body_sha256':'a'*64,'timestamp_basis':'http_response_body_completed'}
    def test_actual_and_legacy(self):
        d=self.fixture(); meta=self.meta(); rows=c.normalize(d,meta,date(2025,1,1),date(2026,12,31)); self.assertEqual(rows[0]['timing_basis'],'actual_receipt'); self.assertEqual(rows[0]['payload']['settlement_rule'],'unknown')
        meta['legacy_cache']=True; self.assertEqual(c.normalize(d,meta,date(2025,1,1),date(2026,12,31))[0]['timing_basis'],'retrospective')
        d['currentLottery']['saleProgress']=False; meta.pop('legacy_cache'); self.assertEqual(c.normalize(d,meta,date(2025,1,1),date(2026,12,31))[0]['timing_basis'],'retrospective')
    def test_excludes_handicap_and_missing_odds(self):
        d=self.fixture(); keys=d['compSchedules']['keys']; vals=d['compSchedules']['datas'][0]; vals[keys.index('handi')]=1
        self.assertEqual(c.normalize(d,self.meta(),date(2025,1,1),date(2026,12,31)),[])
        vals[keys.index('handi')]=0; vals[keys.index('drawAllot')]=0
        self.assertEqual(c.normalize(d,self.meta(),date(2025,1,1),date(2026,12,31)),[])
    def test_cache_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); raw=b'abc'; (p/'a.gz').write_bytes(gzip.compress(raw)); meta={'body_file':'a.gz','body_sha256':c.digest(raw)}
            self.assertEqual(c.body_from_cache(p,meta),raw); meta['body_sha256']='0'*64
            with self.assertRaises(ValueError): c.body_from_cache(p,meta)
    def test_wrong_round_and_duplicate(self):
        d=self.fixture(); d['currentLottery']['gmTs']=260131
        with self.assertRaises(ValueError): c.normalize(d,self.meta(),date(2025,1,1),date(2026,12,31))
        d=self.fixture(); d['compSchedules']['datas'].append(d['compSchedules']['datas'][0])
        with self.assertRaises(ValueError): c.normalize(d,self.meta(),date(2025,1,1),date(2026,12,31))

    def test_403_stops_all_capture_ids(self):
        from argparse import Namespace
        class Response:
            status_code = 403
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): yield b'forbidden'
        args = Namespace(capture_id='one', start='2025-01-01', end='2026-11-04', round='260040', page_size=20, max_requests=1, total_timeout=10)
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'EVIDENCE', Path(tmp)), patch.object(c.requests, 'post', return_value=Response()) as post:
            with self.assertRaises(PermissionError): c.run(args)
            state=json.loads((Path(tmp)/'one/state.json').read_text(encoding='utf8'))
            self.assertEqual(state['events'][-1]['status'],'interrupted')
            self.assertEqual(state['next_round_cursor'],0)
            args.capture_id='two'
            with self.assertRaises(RuntimeError): c.run(args)
            self.assertEqual(post.call_count,1)
            meta=json.loads((Path(tmp)/'one/round-260040.json').read_text(encoding='utf8'))
            self.assertEqual(c.body_from_cache(Path(tmp)/'one',meta),b'forbidden')

    def test_request_cap_resume_and_cache(self):
        from argparse import Namespace
        class Response:
            status_code=200
            def __init__(self, body): self.body=body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): yield self.body
        page={'schedules':{'data':[{'gmOsidTsYear':2026,'gmTs':260130}], 'recordsFiltered':1}}
        args=Namespace(capture_id='batch',start='2025-01-01',end='2026-11-04',round=None,page_size=20,max_requests=1,total_timeout=10)
        with tempfile.TemporaryDirectory() as tmp, patch.object(c,'EVIDENCE',Path(tmp)), patch.object(c,'old_detail',return_value=None), patch.object(c.requests,'post',side_effect=[Response(json.dumps(page).encode()),Response(json.dumps(self.fixture()).encode())]) as post:
            state=c.run(args); self.assertEqual(state['events'][-1]['status'],'interrupted'); self.assertEqual(state['next_round_cursor'],0)
            state=c.run(args); self.assertEqual(state['events'][-1]['status'],'completed'); self.assertEqual(state['next_round_cursor'],1); self.assertEqual(post.call_count,2)

if __name__=='__main__': unittest.main()
