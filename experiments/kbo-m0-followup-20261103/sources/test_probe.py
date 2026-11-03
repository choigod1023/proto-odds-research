import gzip,json,unittest,tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import requests
import probe

ROOT=Path(__file__).resolve().parent

class ProbeTest(unittest.TestCase):
    def test_retry_backoff_cache_and_403_stop(self):
        class Response:
            def __init__(self,status):self.status_code=status;self.content=b'{}';self.headers={'Content-Type':'application/json'}
        class Session:
            def __init__(self,sequence):self.headers={};self.sequence=iter(sequence);self.calls=0
            def post(self,*args,**kwargs):
                self.calls+=1;x=next(self.sequence)
                if isinstance(x,Exception):raise x
                return Response(x)
        for first in (429,requests.Timeout()):
            with self.subTest(first=type(first).__name__):
                s=Session([first,200])
                with tempfile.TemporaryDirectory() as folder,patch.object(probe,'ROOT',Path(folder)),patch.object(probe.requests,'Session',return_value=s),patch.object(probe.time,'sleep') as sleep:
                    f=probe.Fetcher();raw,_=f.fetch('x','https://example.test',{'date':'20261103'});self.assertEqual(raw,b'{}');self.assertEqual(s.calls,2);self.assertTrue(any(x.args==(15,) for x in sleep.call_args_list));f.fetch('x','https://example.test',{'date':'20261103'});self.assertEqual(s.calls,2)
        s=Session([403])
        with tempfile.TemporaryDirectory() as folder,patch.object(probe,'ROOT',Path(folder)),patch.object(probe.requests,'Session',return_value=s),patch.object(probe.time,'sleep'):
            f=probe.Fetcher()
            with self.assertRaises(probe.Stop):f.fetch('x','https://example.test',{})
            with self.assertRaises(probe.Stop):f.fetch('y','https://example.test',{})
            self.assertEqual(s.calls,1)

    def test_classification_uses_receipt_and_source_codes(self):
        game={'G_DT':'20261103','G_TM':'18:30','CANCEL_SC_ID':'0','GAME_STATE_SC':'1','GAME_RESULT_CK':0}
        self.assertEqual(probe.classify(game,'2026-11-03T08:17:00+00:00'),'future')
        self.assertEqual(probe.classify(game,'2026-11-03T10:00:00+00:00'),'pending_past_kickoff')
        self.assertEqual(probe.classify(game|{'GAME_STATE_SC':'3','GAME_RESULT_CK':1},'2026-11-03T08:17:00+00:00'),'completed')
        self.assertEqual(probe.classify(game|{'CANCEL_SC_ID':'1'},'2026-11-03T08:17:00+00:00'),'cancelled_or_suspended_source_code')
        self.assertEqual(probe.classify(game|{'CANCEL_SC_ID':None},'2026-11-03T08:17:00+00:00'),'unknown_missing_cancel_code')

    def test_unconfirmed_recent_lineup_is_not_substituted(self):
        self.assertEqual(probe.parse_lineup(json.dumps([[{'LINEUP_CK':False}],[],[],[],[]]).encode(),{}),{'status':'not_confirmed','ordered_nine':None})
        game={'START_PIT_CK':0,'B_PIT_P_ID':1,'B_PIT_P_NM':'A','T_PIT_P_ID':2,'T_PIT_P_NM':'B'}
        self.assertEqual(probe.starter_metadata(game)['announced'],{})
        self.assertEqual(probe.starter_metadata(game|{'START_PIT_CK':1})['status'],'announced_both')

    def test_confirmed_lineup_requires_game_team_and_all_nine(self):
        g={'G_ID':'g','HOME_ID':'H','AWAY_ID':'A','LE_ID':1,'SR_ID':7,'SEASON_ID':2026}
        def meta(t):return [{'G_ID':'g','T_ID':t,'LE_ID':1,'SR_ID':7,'SEASON_ID':2026}]
        def table(n):return [json.dumps({'rows':[{'row':[{'Text':str(i)},{'Text':'pos'},{'Text':'name'+str(i)}]} for i in range(1,n+1)]})]
        body=[[{'LINEUP_CK':True}],meta('H'),meta('A'),table(9),table(9)]
        self.assertEqual(probe.parse_lineup(json.dumps(body).encode(),g)['status'],'confirmed')
        body[2][0]['T_ID']='H'
        with self.assertRaises(ValueError):probe.parse_lineup(json.dumps(body).encode(),g)
        body[2][0]['T_ID']='A';body[4]=table(8)
        with self.assertRaises(ValueError):probe.parse_lineup(json.dumps(body).encode(),g)

    def test_captured_requests_raw_and_window(self):
        manifest=json.loads((ROOT/'manifest.json').read_bytes());observations=json.loads((ROOT/'observations.json').read_bytes());ledger=json.loads((ROOT/'requests.json').read_bytes())
        self.assertEqual(probe.sha((ROOT/'observations.json').read_bytes()),manifest['observations_sha256']);self.assertEqual(probe.sha((ROOT/'requests.json').read_bytes()),manifest['requests_sha256'])
        self.assertEqual(len(ledger),20);self.assertEqual(len(observations['day_status']),14);self.assertEqual(manifest['failed_or_unobserved_days'],0)
        self.assertEqual(manifest['counts']['completed'],3);self.assertEqual(manifest['counts']['future'],6);self.assertEqual(sum(manifest['counts'].values()),9);self.assertEqual(manifest['counts']['cancelled_or_suspended_source_code'],0);self.assertEqual(manifest['future_lineup_status'],{'not_confirmed':6})
        previous=None
        for rec in ledger:
            self.assertEqual(rec['http_status'],200);self.assertEqual(rec['method'],'POST');self.assertIsNotNone(datetime.fromisoformat(rec['received_at']).utcoffset())
            start=datetime.fromisoformat(rec['requested_at'])
            if previous:self.assertGreaterEqual((start-previous).total_seconds(),1.5)
            previous=start;raw=gzip.decompress((ROOT/rec['body_file']).read_bytes());self.assertEqual(probe.sha(raw),rec['sha256'])
            if rec['name'].startswith('schedule-'):
                self.assertEqual(rec['params']['srId'],'0,1,3,4,5,6,7,8,9');self.assertEqual(str(json.loads(raw)['code']),'100')
            else:self.assertIs(json.loads(raw)[0][0]['LINEUP_CK'],False)
        self.assertEqual(len({g['official_game_id'] for g in observations['games']}),9)
        self.assertTrue(all(g['lineup']['ordered_nine'] is None for g in observations['games'] if g['classification']=='future'))
        self.assertFalse(observations['prediction_executed']);self.assertFalse(observations['price_source_connected'])

if __name__=='__main__':unittest.main()
