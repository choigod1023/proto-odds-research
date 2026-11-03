import gzip
import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import restore

ROOT = Path(__file__).resolve().parent


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.game = {'date':'2023-04-14','home':'KT','away':'한화','hg':7,'ag':7}
        self.row = {'kickoff_at':'2023-04-14T18:30:00+09:00'}
        self.candidate = {'leagueCode':'BS001','handi':0,'homeId':'KT','awayId':'HH',
            'drawAllot':0,'gameSubject':None,'gameReject':'0','buyReject':'0',
            'mchScore':'7:7','gameDate':1681464600000,'winAllot':1.36,'loseAllot':2.49}

    def test_exact_match(self):
        self.assertTrue(restore.match(self.row,self.game,self.candidate))

    def test_modern_winner_market_type_is_21(self):
        modern = self.candidate | {'betId':'2', 'betTypId':'2', 'handi':21}
        self.assertTrue(restore.match(self.row, self.game, modern))
        self.assertFalse(restore.match(self.row, self.game, modern | {'betId':'7'}))
        self.assertFalse(restore.match(self.row, self.game, modern | {'betTypId':'7'}))

    def test_refund_cancel_other_market_or_bad_identity_rejected(self):
        for change in ({'winAllot':1}, {'gameReject':'1'}, {'buyReject':'1'},
                       {'handi':-1.5}, {'drawAllot':3.2}, {'gameSubject':'합계'},
                       {'mchScore':'1:1'}, {'homeId':'SS'}, {'leagueCode':'BS004'},
                       {'gameDate':1681464600000+60000}):
            with self.subTest(change=change):
                self.assertFalse(restore.match(self.row,self.game,self.candidate | change))

    def test_captured_official_samples(self):
        samples=[(2023,43,28,'KT','한화',7,7,'2023-04-14T18:30:00+09:00',1.36,2.49),
                 (2024,39,1,'LG','삼성',2,2,'2024-03-27T18:30:00+09:00',1.57,2.05),
                 (2026,40,192,'삼성','두산',5,5,'2026-03-31T18:30:00+09:00',1.61,1.89)]
        for y,rd,num,home,away,hg,ag,kick,hp,ap in samples:
            with self.subTest(year=y):
                raw=gzip.decompress((ROOT/'evidence'/f'detail-{y}-{rd}.body.gz').read_bytes())
                d=json.loads(raw); c=d['compSchedules']
                values=[dict(zip(c['keys'],x)) for x in c['datas']]
                v=next(x for x in values if x['matchSeq']==num)
                self.assertTrue(restore.match({'kickoff_at':kick},
                    {'date':kick[:10],'home':home,'away':away,'hg':hg,'ag':ag},v))
                self.assertEqual((v['winAllot'],v['loseAllot']),(hp,ap))

    def test_evidence_and_output_provenance(self):
        attempts=json.loads((ROOT/'evidence/attempts.json').read_text(encoding='utf8'))
        for a in attempts:
            if a.get('http_status') != 200:
                continue
            raw=gzip.decompress((ROOT/a['body_file']).read_bytes())
            self.assertEqual(restore.sha(raw),a['body_sha256'])
        output=restore.read_jsonl(ROOT/'restored-prices.jsonl')
        manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf8'))
        self.assertEqual(restore.sha((ROOT/'restored-prices.jsonl').read_bytes()),manifest['output_sha256'])
        self.assertEqual(len(output),manifest['restored'])
        self.assertEqual(len({r['fixture_id'] for r in output}),len(output))
        self.assertEqual(len({r['market_id'] for r in output}),len(output))
        self.assertTrue(all(r['source_observed_at'] is None and not r['execution_price_verified'] for r in output))
        byfile={a['body_file']:a for a in attempts if a.get('http_status')==200}
        for row in output:
            self.assertEqual(row['response_sha256'],byfile[row['response_file']]['body_sha256'])
            self.assertEqual(row['received_at'],byfile[row['response_file']]['received_at'])

    def test_all_restored_rows_against_official_response(self):
        output=restore.read_jsonl(ROOT/'restored-prices.jsonl')
        snapshots={}
        for row in output:
            path=row['response_file']
            if path not in snapshots:
                raw=gzip.decompress((ROOT/path).read_bytes())
                data=json.loads(raw); comp=data['compSchedules']
                snapshots[path]=(data, [dict(zip(comp['keys'], values)) for values in comp['datas']])
            data, candidates=snapshots[path]
            self.assertEqual(data['gmTs'],row['official_round_id'])
            matches=[x for x in candidates if x['matchSeq']==row['match_seq']]
            self.assertEqual(len(matches),1)
            source=matches[0]
            self.assertEqual((source['homeId'],source['awayId'],source['mchScore']),
                             (row['official_home_id'],row['official_away_id'],row['score']))
            self.assertEqual((source['winAllot'],source['loseAllot']),
                             (row['prices']['home'],row['prices']['away']))
            self.assertEqual((source['betId'],source['betTypId'],source['handi']),
                             (row['source_market_type']['betId'],row['source_market_type']['betTypId'],row['source_market_type']['handi']))
            self.assertEqual((source['gameResult'],source['protoStatus'],source['gameReject'],source['buyReject']),
                             (row['source_result_code'],row['source_status'],row['source_reject']['game'],row['source_reject']['buy']))
            self.assertEqual(datetime.fromtimestamp(source['gameDate']/1000,restore.KST).isoformat(),row['game_at'])
        self.assertEqual(len(output),373)


if __name__=='__main__': unittest.main()
