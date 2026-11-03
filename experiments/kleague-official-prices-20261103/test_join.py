import gzip
import json
import unittest
from datetime import datetime
from pathlib import Path
import join

ROOT=Path(__file__).resolve().parent

class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.f={'year':2026,'leagueId':1,'gameId':1,'gameStatus':'FE','endYn':'Y',
                'gameDate':'2026.02.28','gameTime':'14:00','homeTeam':'K18','homeTeamName':'인천',
                'awayTeam':'K09','awayTeamName':'서울','homeGoal':1,'awayGoal':2}
        dt=datetime(2026,2,28,14,0,tzinfo=join.KST)
        self.c={'leagueCode':'SC001','homeId':'K18','awayId':'K09','homeName':'인천유나이티드','awayName':'FC서울',
                'betId':'1','betTypId':'1','betNm':'축구 승무패','handi':0,'gameSubject':None,
                'winHandi':0,'drawHandi':0,'loseHandi':0,'gameReject':'0','buyReject':'0',
                'gameDate':int(dt.timestamp()*1000),'mchScore':'1:2','gameResult':'2',
                'winAllot':2.3,'drawAllot':3.2,'loseAllot':2.6}

    def test_valid_and_legacy_market(self):
        self.assertTrue(join.strict_match(self.f,self.c))
        self.assertTrue(join.strict_match(self.f,self.c|{'betId':None,'betTypId':None,'betNm':None}))

    def test_multiple_candidates_are_excluded(self):
        self.assertIsNone(join.unique_candidate([]))
        self.assertIsNone(join.unique_candidate([self.c,self.c|{'winAllot':9.5}]))
        self.assertEqual(join.unique_candidate([self.c]),self.c)

    def test_excluded_markets_and_missing_prices(self):
        for change in ({'betId':'118','betNm':'축구 전반 승무패'}, {'betTypId':'4'},
                       {'handi':2}, {'gameSubject':'전반'}, {'drawAllot':0},
                       {'winAllot':1}, {'loseAllot':None}, {'gameReject':'1'}, {'buyReject':'2'}):
            with self.subTest(change=change):self.assertFalse(join.strict_match(self.f,self.c|change))

    def test_wrong_team_time_result_or_league(self):
        for change in ({'homeId':'K09','awayId':'K18'}, {'homeName':'수원FC'},
                       {'gameDate':self.c['gameDate']+3600000}, {'mchScore':'2:1'},
                       {'gameResult':'0'}, {'leagueCode':'SC003'}):
            with self.subTest(change=change):self.assertFalse(join.strict_match(self.f,self.c|change))
        self.assertFalse(join.strict_match(self.f|{'gameStatus':''},self.c))
        self.assertFalse(join.strict_match(self.f|{'endYn':'N'},self.c))
        self.assertFalse(join.strict_match(self.f|{'homeTeam':'K02','homeTeamName':'수원FC'},self.c))

    def test_output_provenance_and_unique_markets(self):
        manifest=json.loads((ROOT/'manifest.json').read_bytes());data=(ROOT/'joined-prices.jsonl.gz').read_bytes();raw=gzip.decompress(data)
        self.assertEqual(join.sha(data),manifest['joined_gzip_sha256']);self.assertEqual(join.sha(raw),manifest['joined_uncompressed_sha256'])
        for name,key in [('coverage.json','coverage_sha256'),('exclusions.jsonl','exclusions_sha256'),('team-crosswalk.json','team_crosswalk_sha256'),('inputs/sources.json','source_manifest_sha256'),('evidence/attempts.json','ledger_sha256')]:
            self.assertEqual(join.sha((ROOT/name).read_bytes()),manifest[key])
        records=[json.loads(x) for x in raw.splitlines()];self.assertEqual(len(records),manifest['matched'])
        self.assertEqual(len({x['fixture_id'] for x in records}),len(records));self.assertEqual(len({(x['gmTs'],x['matchSeq']) for x in records}),len(records))
        fixtures=join.load_fixtures(); cache={}
        ledger=json.loads((ROOT/'evidence/attempts.json').read_bytes());byfile={x['body_file']:x for x in ledger if x.get('http_status')==200}
        byname={x['name']:x for x in ledger if x.get('http_status')==200}
        for response in byfile.values():
            self.assertEqual(join.sha(join.readgz(ROOT/response['body_file'])),response['body_sha256'])
        for name,digest in manifest['official_response_sha256'].items():
            self.assertEqual(join.sha(join.readgz(ROOT/byname[name]['body_file'])),digest)
        self.assertLessEqual(manifest['new_request_count'],manifest['max_new_requests'])
        excluded=[json.loads(line) for line in (ROOT/'exclusions.jsonl').read_bytes().splitlines()]
        self.assertEqual(len(records)+len(excluded),manifest['fixture_count'])
        self.assertEqual(len({x['fixture_id'] for x in excluded}),len(excluded))
        self.assertFalse({x['fixture_id'] for x in records}&{x['fixture_id'] for x in excluded})
        for row in records:
            year,league,gid=(int(x) for x in row['fixture_id'].split(':')[1:]); f=fixtures[(year,league,gid)];file=row['response_file']
            if file not in cache:
                raw=join.readgz(ROOT/file);self.assertEqual(join.sha(raw),byfile[file]['body_sha256']);d=json.loads(raw);v=d['compSchedules'];cache[file]=(d,[dict(zip(v['keys'],x)) for x in v['datas']])
            d,markets=cache[file];m=[x for x in markets if x['matchSeq']==row['matchSeq']];self.assertEqual(len(m),1);c=m[0]
            self.assertTrue(join.strict_match(f,c));self.assertEqual(d['gmTs'],row['gmTs']);self.assertEqual(row['prices'],{'home':c['winAllot'],'draw':c['drawAllot'],'away':c['loseAllot']})
            self.assertEqual(row['response_sha256'],byfile[file]['body_sha256']);self.assertEqual(row['received_at'],byfile[file]['received_at']);self.assertIsNone(row['price_observed_at']);self.assertFalse(row['execution_price_verified'])

if __name__=='__main__':unittest.main()
