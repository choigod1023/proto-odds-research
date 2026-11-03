import gzip,json,unittest
from datetime import datetime
from pathlib import Path
import join

ROOT=Path(__file__).resolve().parent

class TestJoin(unittest.TestCase):
    def setUp(self):
        self.f={'league':'KBL','season':'2025-26','fixture_id':'x','state':'completed','kickoff':'2025-10-03T14:00:00+09:00','home':'50','away':'55','score':[91,89]}
        self.r={'leagueCode':'BK001','homeId':'50','awayId':'55','homeName':'창원LG세이커스','awayName':'서울SK나이츠','gameDate':int(datetime.fromisoformat(self.f['kickoff']).timestamp()*1000),'mchScore':'91:89','gameResult':'0','betId':'3','betTypId':'2','betNm':'농구 승패','handi':21,'gameSubject':None,'winHandi':0,'drawHandi':0,'loseHandi':0,'winAllot':1.6,'drawAllot':0,'loseAllot':2.2,'gameReject':'0','buyReject':'0'}
    def test_basketball_full_final_including_overtime(self):
        self.assertTrue(join.candidate(self.f,self.r))
        for change in ({'betId':'109','betTypId':'3','betNm':'농구 승5패','handi':0}, {'handi':2},{'winHandi':1.5}, {'homeId':'55','awayId':'50'}, {'gameDate':self.r['gameDate']+60000},{'mchScore':'80:80'}):
            with self.subTest(change=change):self.assertFalse(join.candidate(self.f,self.r|change))
    def test_volleyball_full_match_not_set_or_handicap(self):
        f=self.f|{'league':'VLEAGUE_M','home':'1001','away':'1006','score':[3,2]}
        r=self.r|{'leagueCode':'VL001','homeId':'1001','awayId':'1006','homeName':'대한항공점보스','awayName':'한국전력빅스톰','mchScore':'3:2','betId':'4','betNm':'배구 승패'}
        self.assertTrue(join.candidate(f,r));self.assertFalse(join.candidate(f,r|{'betId':'7','betNm':'배구 핸디캡'}));self.assertFalse(join.candidate(f,r|{'winHandi':1.5}));self.assertFalse(join.candidate(f,r|{'mchScore':'25:23'}))
    def test_void_restrictions_duplicates_and_missing_flags(self):
        self.assertTrue(join.safe_status(self.r));self.assertFalse(join.safe_status(self.r|{'buyReject':None}));self.assertFalse(join.safe_status(self.r|{'gameReject':'1'}));self.assertFalse(join.safe_status(self.r|{'buyReject':'4'}))
        self.assertIsNone(join.unique([]));self.assertIsNone(join.unique([self.r,self.r|{'winAllot':9.9}]))
        self.assertFalse(join.market('KBL',self.r|{'winAllot':1.0}));self.assertFalse(join.market('KBL',self.r|{'drawAllot':None}));self.assertFalse(join.candidate(self.f|{'state':'doubleheader_ambiguous'},self.r))
    def test_football_90min_not_first_half(self):
        f=self.f|{'league':'J1','home':'gosaka','away':'cosaka','score':[2,2]}
        r=self.r|{'leagueCode':'SC007','homeId':'128','awayId':'133','homeName':'감바오사카','awayName':'세레소오사카','betId':'1','betTypId':'1','betNm':'축구 승무패','handi':0,'drawAllot':3.3,'mchScore':'2:2','gameResult':'1'}
        self.assertTrue(join.candidate(f,r));self.assertFalse(join.candidate(f,r|{'betId':'118','betNm':'축구 전반 승무패'}))
    def test_npb_draw_refund_and_doubleheader(self):
        f=self.f|{'league':'NPB','home':'t','away':'c','score':[2,2]}
        r=self.r|{'leagueCode':'BS004','homeId':'C1','awayId':'C2','betId':'2','betNm':'야구 승패','mchScore':'2:2','gameResult':'4'}
        self.assertTrue(join.candidate(f,r));self.assertFalse(join.candidate(f,r|{'gameResult':'1'}));self.assertFalse(join.candidate(f|{'state':'doubleheader_ambiguous'},r))
    def test_every_output_against_sources_and_evidence(self):
        m=json.loads((ROOT/'manifest.json').read_bytes())
        for path,key in [('joined-prices.jsonl.gz','joined_sha256'),('exclusions.jsonl.gz','exclusions_sha256'),('coverage.json','coverage_sha256'),('team-crosswalk.json','crosswalk_sha256'),('inputs/sources.json','sources_sha256'),('evidence/attempts.json','ledger_sha256')]:self.assertEqual(join.sha((ROOT/path).read_bytes()),m[key])
        raw=join.readgz(ROOT/'joined-prices.jsonl.gz');self.assertEqual(join.sha(raw),m['joined_uncompressed_sha256']);rows=[json.loads(x) for x in raw.splitlines()];ex=[json.loads(x) for x in join.readgz(ROOT/'exclusions.jsonl.gz').splitlines()]
        self.assertEqual(len(rows),m['joined']);self.assertEqual(len(rows)+len(ex),m['source_fixtures']);self.assertEqual(len({(x['gmTs'],x['matchSeq']) for x in rows}),len(rows));self.assertFalse({x['fixture_id'] for x in rows}&{x['fixture_id'] for x in ex});self.assertLessEqual(m['new_requests'],200)
        fs={(x['league'],x['fixture_id']):x for x in join.fixtures()};ledger=json.loads((ROOT/'evidence/attempts.json').read_bytes());byfile={r['body_file']:r for r in ledger if r.get('http_status')==200};cache={}
        for rec in byfile.values():self.assertEqual(join.sha(join.readgz(ROOT/rec['body_file'])),rec['body_sha256'])
        for x in rows:
            p=x['response_file'];meta=byfile[p]
            if p not in cache:
                d=json.loads(join.readgz(ROOT/p));v=d['compSchedules'];cache[p]=(d,[dict(zip(v['keys'],r)) for r in v['datas']])
            d,vs=cache[p];found=[r for r in vs if r['matchSeq']==x['matchSeq']];self.assertEqual(len(found),1);r=found[0];f=fs[(x['league'],x['fixture_id'])]
            self.assertEqual(d['gmTs'],x['gmTs']);self.assertTrue(join.candidate(f,r));self.assertTrue(join.safe_status(r));self.assertEqual(x['prices'],{'home':r['winAllot'],'draw':r['drawAllot'],'away':r['loseAllot']});self.assertEqual(x['response_sha256'],meta['body_sha256']);self.assertEqual(x['received_at'],meta['received_at']);self.assertIsNone(x['price_observed_at']);self.assertFalse(x['execution_price_verified'])
            if x['draw_void_refund']:self.assertEqual(x['league'],'NPB');self.assertEqual(f['score'][0],f['score'][1]);self.assertEqual(r['gameResult'],'4')

if __name__=='__main__':unittest.main()
