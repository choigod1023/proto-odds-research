import gzip
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path
import audit

ROOT=Path(__file__).resolve().parent

class AuditTest(unittest.TestCase):
    def setUp(self):
        self.target={'fixture_id':'x','kickoff_at':'2026-03-28T14:00:00+09:00',
          'home_id':'KIA','away_id':'SSG','score':[7,6],'original_prices':{'home':1.73,'away':1.75}}
        self.record={'matchSeq':21,'leagueCode':'BS001','homeId':'HT','awayId':'SK',
          'gameDate':int(audit.datetime.fromisoformat(self.target['kickoff_at']).timestamp()*1000),
          'betId':'2','betTypId':'2','handi':21,'drawAllot':0,'gameSubject':None,'gameReject':'0','buyReject':'0',
          'mchScore':'7:6','gameResult':'0','winAllot':1.73,'loseAllot':1.75}

    def test_equal_and_zero_padded_number(self):
        self.assertEqual(audit.classify(self.target,[self.record],'021')[0],'equal')
        self.assertEqual(audit.classify(self.target,[self.record|{'betId':None,'betTypId':None,'handi':0}],'21')[0],'equal')

    def test_changed_identity_product_result_and_missing(self):
        cases=[({'winAllot':1.8},'changed'),({'homeId':'SS'},'identity_mismatch'),
          ({'gameDate':self.record['gameDate']+60000},'identity_mismatch'),
          ({'betId':'7'},'product_mismatch'),({'drawAllot':3.1},'product_mismatch'),
          ({'mchScore':'0:0'},'result_mismatch'),({'gameResult':'2'},'result_mismatch'),
          ({'winAllot':1.0},'settlement_or_sales_status'),({'gameReject':'1'},'settlement_or_sales_status')]
        for change,expected in cases:
            with self.subTest(change=change):self.assertEqual(audit.classify(self.target,[self.record|change],'21')[0],expected)
        self.assertEqual(audit.classify(self.target,[],'21')[0],'not_found')
        self.assertEqual(audit.classify(self.target,[self.record,self.record],'21')[0],'identity_mismatch')
        self.assertEqual(audit.classify(self.target,[self.record|{'buyReject':'1'}],'21')[0],'equal')
        away=self.target|{'score':[1,2]};self.assertEqual(audit.classify(away,[self.record|{'mchScore':'1:2','gameResult':'2'}],'21')[0],'equal')

    def test_original_cohort_and_all_354_official_rows(self):
        man=json.loads((ROOT/'audit-manifest.json').read_bytes())
        for name,key in [('audit-sources.json','sources_sha256'),('audit-targets.jsonl.gz','targets_sha256'),('test-fixture-ids.json.gz','test_ids_sha256'),('request-ledger.json','ledger_sha256'),('audit-original-2026-prices.jsonl.gz','audit_gzip_sha256')]:
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),man[key])
        data=gzip.decompress((ROOT/'audit-original-2026-prices.jsonl.gz').read_bytes())
        self.assertEqual(hashlib.sha256(data).hexdigest(),man['audit_uncompressed_sha256'])
        rows=[json.loads(line) for line in data.splitlines()];targets=[json.loads(x) for x in gzip.decompress((ROOT/'audit-targets.jsonl.gz').read_bytes()).splitlines()]
        ids=set(json.loads(gzip.decompress((ROOT/'test-fixture-ids.json.gz').read_bytes())));self.assertEqual(len(ids),726)
        self.assertEqual(len(rows),354);self.assertEqual({x['fixture_id'] for x in rows},{x['fixture_id'] for x in targets});self.assertTrue({x['fixture_id'] for x in rows}<=ids)
        target_by_id={x['fixture_id']:x for x in targets};ledger=json.loads((ROOT/'request-ledger.json').read_bytes());responses={}
        for rec in ledger:
            if rec.get('http_status')!=200:continue
            raw=gzip.decompress((ROOT/rec['body_file']).read_bytes());self.assertEqual(hashlib.sha256(raw).hexdigest(),rec['body_sha256']);d=json.loads(raw);c=d['compSchedules'];responses[rec['body_file']]=(rec,d,[dict(zip(c['keys'],v)) for v in c['datas']])
        self.assertEqual(len(responses),38)
        for row in rows:
            with self.subTest(fixture=row['fixture_id']):
                t=target_by_id[row['fixture_id']];rec,d,markets=responses[row['response_file']];m=[x for x in markets if str(x.get('matchSeq','')).isdigit() and int(x['matchSeq'])==int(t['market_id'].split(':')[3])]
                self.assertEqual(len(m),1);v=m[0]
                self.assertEqual(d['gmTs'],int(t['market_id'].split(':')[1][2:]+f"{int(t['market_id'].split(':')[2]):04d}"))
                self.assertEqual((v['leagueCode'],v['homeId'],v['awayId']),('BS001',audit.TEAM[t['home_id']],audit.TEAM[t['away_id']]))
                self.assertEqual(audit.datetime.fromtimestamp(v['gameDate']/1000,audit.KST),audit.datetime.fromisoformat(t['kickoff_at']))
                self.assertTrue((v['betId']=='2' and v['betTypId']=='2') or (v['betId'] is None and v['betTypId'] is None and v['handi']==0));self.assertEqual(v['drawAllot'],0)
                self.assertEqual(v['mchScore'],f"{t['score'][0]}:{t['score'][1]}")
                self.assertEqual((Decimal(str(v['winAllot'])),Decimal(str(v['loseAllot']))),(Decimal(str(t['original_prices']['home'])),Decimal(str(t['original_prices']['away']))))
                self.assertEqual(row['original_prices'],t['original_prices']);self.assertEqual(row['official_prices'],{'home':v['winAllot'],'away':v['loseAllot']});self.assertEqual(row['response_sha256'],rec['body_sha256']);self.assertEqual(row['official_received_at'],rec['received_at']);self.assertIsNone(row['official_price_observed_at'])
        self.assertEqual(man['counts'],{'equal':354});self.assertEqual(man['official_selection_restriction_count'],7)

if __name__=='__main__':unittest.main()
