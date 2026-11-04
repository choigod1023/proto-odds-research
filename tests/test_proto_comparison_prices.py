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

    def test_offline_rebuild_pins_source_and_never_requests(self):
        d=self.fixture(); raw=json.dumps(d,ensure_ascii=False).encode(); ts='260130'
        meta=self.meta(); meta['body_sha256']=c.digest(raw); meta['timestamp_basis']='legacy_unverified'; meta['legacy_cache']=True
        records=c.normalize(d,meta,date(2025,1,1),date(2026,12,31))
        expected=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in records).encode()
        source={'round':ts,'source_root_index':0,'body_file':'raw.gz','body_sha256':c.digest(raw),'source_url':c.DETAIL,'received_at':meta['received_at']}
        manifest={'start':'2025-01-01','end':'2026-12-31','sources':[source],'record_count':1,'normalized_sha256':c.digest(expected)}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'raw.gz').write_bytes(gzip.compress(raw)); (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
            with patch.object(c,'OLD',[root]), patch.object(c.requests,'post',side_effect=AssertionError('network used')):
                result=c.offline_cache(root/'manifest.json',root/'out.gz')
                self.assertEqual(result['record_count'],1); self.assertEqual(gzip.decompress((root/'out.gz').read_bytes()),expected)
                v2=c.normalize_v2(d,meta,date(2025,1,1),date(2026,12,31))
                v2raw=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in v2).encode()
                v2manifest={'sources':[source],'v1_normalized_sha256':c.digest(expected),'record_count':1,'normalized_sha256':c.digest(v2raw)}
                (root/'manifest-v2.json').write_text(json.dumps(v2manifest),encoding='utf8')
                c.offline_cache(root/'manifest.json',root/'out-v2.gz','v2',root/'manifest-v2.json')
                self.assertEqual(gzip.decompress((root/'out-v2.gz').read_bytes()),v2raw)
                v2manifest['v1_normalized_sha256']='bad'; (root/'manifest-v2.json').write_text(json.dumps(v2manifest),encoding='utf8')
                with self.assertRaisesRegex(ValueError,'v1 reference'): c.offline_cache(root/'manifest.json',root/'bad-v2.gz','v2',root/'manifest-v2.json')
                (root/'raw.gz').write_bytes(gzip.compress(b'wrong'))
                with self.assertRaisesRegex(ValueError,'SHA-256'): c.offline_cache(root/'manifest.json',root/'other.gz')
                self.assertFalse((root/'other.gz').exists())

    def test_body_limit_and_completion_timestamp(self):
        from argparse import Namespace
        args=Namespace(capture_id='large',start='2025-01-01',end='2026-12-31',round='260130',page_size=20,max_requests=1,total_timeout=10,output=None,evidence=None)
        class Response:
            status_code=200
            def __init__(self,chunks,mark): self.chunks=chunks; self.mark=mark
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size):
                yield from self.chunks
                self.mark.append('complete')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); args.evidence=root
            with patch.object(c,'EVIDENCE',root), patch.object(c.requests,'post',return_value=Response([b'x' * c.MAX_BODY_BYTES,b'x'],[])):
                with self.assertRaisesRegex(ValueError,'16 MiB'): c.run(args)
            state=json.loads((root/'large/state.json').read_text(encoding='utf8'))
            self.assertEqual(state['events'][-1]['status'],'failed'); self.assertFalse((root/'large/round-260130.body.gz').exists())
            mark=[]; args.capture_id='complete'; args.output=root/'combined.jsonl'
            body=json.dumps(self.fixture(),ensure_ascii=False).encode()
            def clock(): return '2026-11-04T00:00:02+00:00' if mark else '2026-11-04T00:00:01+00:00'
            with patch.object(c,'EVIDENCE',root), patch.object(c,'now',side_effect=clock), patch.object(c.requests,'post',return_value=Response([body[:20],body[20:]],mark)):
                c.run(args)
            meta=json.loads((root/'complete/round-260130.json').read_text(encoding='utf8'))
            self.assertEqual(meta['received_at'],'2026-11-04T00:00:02+00:00')
            self.assertEqual(meta['timestamp_basis'],'http_response_body_completed')
            self.assertEqual(c.body_from_cache(root/'complete',meta),body)
            self.assertTrue(args.output.exists())

    def test_v2_source_identity_preserves_v1_and_fallback(self):
        d=self.fixture(); keys=d['compSchedules']['keys']; values=d['compSchedules']['datas'][0]
        for key,value in {'leagueName':'KBL','homeId':'10','awayId':'20'}.items():
            keys.append(key); values.append(value)
        a=c.normalize(d,self.meta(),date(2025,1,1),date(2026,12,31))[0]
        b=c.normalize_v2(d,self.meta(),date(2025,1,1),date(2026,12,31))[0]
        identity=b['payload'].pop('source_identity')
        self.assertEqual(identity['homeId'],'10'); self.assertEqual(identity['awayId'],'20')
        self.assertEqual(identity['leagueCode'],'SC001'); self.assertEqual(identity['leagueName'],'KBL')
        self.assertEqual(identity['gameDate'],values[keys.index('gameDate')]); self.assertEqual(identity['betNm'],'축구 승무패')
        self.assertEqual(identity['league_basis'],'leagueName'); self.assertEqual(b['league'],'KBL')
        b['league']=identity['leagueCode']; self.assertEqual(a,b)
        values[keys.index('leagueName')]=''
        fallback=c.normalize_v2(d,self.meta(),date(2025,1,1),date(2026,12,31))[0]
        self.assertEqual(fallback['league'],'SC001'); self.assertEqual(fallback['payload']['source_identity']['league_basis'],'leagueCode_fallback')
        self.assertEqual(fallback['payload']['source_identity']['leagueName'],'')

    def test_online_v2_identity_with_completed_response(self):
        from argparse import Namespace
        d=self.fixture(); d['compSchedules']['keys'].extend(['leagueName','homeId','awayId']); d['compSchedules']['datas'][0].extend(['KBL','10','20'])
        class Response:
            status_code=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size): yield json.dumps(d,ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); args=Namespace(capture_id='v2',start='2025-01-01',end='2026-12-31',round='260130',page_size=20,max_requests=1,total_timeout=10,output=root/'out.jsonl',evidence=root,schema='v2')
            with patch.object(c,'EVIDENCE',root), patch.object(c.requests,'post',return_value=Response()): c.run(args)
            row=json.loads(args.output.read_text(encoding='utf8').splitlines()[0]); self.assertEqual(row['league'],'KBL'); self.assertEqual(row['payload']['source_identity']['homeId'],'10')
            args.schema='v1'
            with patch.object(c,'EVIDENCE',root), self.assertRaises(ValueError): c.run(args)

if __name__=='__main__': unittest.main()
