import gzip,hashlib,json,unittest
from pathlib import Path
from parse import parse
from join import market
R=Path(__file__).resolve().parent
class EvidenceTests(unittest.TestCase):
    def test_selection_is_reproducible_and_spans_season(self):
        d=json.loads((R/'selection.json').read_text(encoding='utf8'));ids=d['official_fixture_ids'];selected=d['selected_ids']
        self.assertEqual(len(ids),380);self.assertEqual(ids,sorted(set(ids)))
        self.assertEqual(selected,[ids[(i*(len(ids)-1))//99] for i in range(100)])
        self.assertLess(selected[0][:2],'04');self.assertGreater(selected[-1][:2],'10')
    def test_actual_raw_three_periods(self):
        d=json.loads((R/'selection.json').read_text(encoding='utf8'));logs=[json.loads(x) for x in (R/'evidence/requests.jsonl').read_text(encoding='utf8').splitlines()]
        for code in [d['selected_ids'][0],d['selected_ids'][50],d['selected_ids'][99]]:
            record=next(x for x in logs if x['kind']=='lineup' and x['key']==code and x.get('status')==200)
            raw=gzip.decompress((R/'evidence'/record['body_file']).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),record['body_sha256'])
            data,label=parse(raw,code,record['received_at_utc'],record['body_sha256'])
            self.assertEqual([len(x) for x in data['starting_xi']],[11,11])
            self.assertFalse(data['strict_pit']);self.assertIsNone(data['formation_pregame'])
            self.assertNotIn('score_reported',data);self.assertEqual(len(label['score_reported']),2)
    def test_market_guard(self):
        row={'betId':None,'betTypId':None,'handi':0,'gameSubject':None,'gameReject':'0','buyReject':'0','winAllot':2,'drawAllot':3,'loseAllot':4}
        self.assertTrue(market(row));self.assertFalse(market(row|{'handi':1}));self.assertFalse(market(row|{'buyReject':'1'}));self.assertFalse(market(row|{'drawAllot':0}))
    def test_missing_profile_link_uses_matching_official_serialized_identity(self):
        code='021401'; logs=[json.loads(x) for x in (R/'evidence/requests.jsonl').read_text(encoding='utf8').splitlines()]
        rec=next(x for x in logs if x.get('kind')=='lineup' and x.get('key')==code)
        raw=gzip.decompress((R/'evidence'/rec['body_file']).read_bytes())
        data,_=parse(raw,code,rec['received_at_utc'],rec['body_sha256'])
        player=next(x for x in data['starting_xi'][1] if x['name']=='ラファエル ハットン')
        self.assertEqual(player['official_player_id'],'1503025')
        self.assertEqual(player['identity_source'],'serialized_roster_lookup')
    def test_joined_prices_exist_in_exact_recorded_original(self):
        rows=[json.loads(x) for x in gzip.decompress((R/'price-joins.jsonl.gz').read_bytes()).splitlines()]
        seen=set()
        for joined in rows:
            path=R/'price-evidence'/joined['price_source']; raw=gzip.decompress(path.read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),joined['price_source_sha256'])
            data=json.loads(raw);cs=data['compSchedules']
            found=[dict(zip(cs['keys'],x)) for x in cs['datas'] if dict(zip(cs['keys'],x)).get('matchSeq')==joined['matchSeq']]
            self.assertEqual(len(found),1);row=found[0]
            self.assertEqual(data['gmTs'],joined['betman_round']);self.assertTrue(market(row))
            self.assertEqual([row['winAllot'],row['drawAllot'],row['loseAllot']],joined['prices'])
            self.assertEqual(row['mchScore'],':'.join(map(str,joined['postgame_score'])))
            key=(joined['betman_round'],joined['matchSeq']);self.assertNotIn(key,seen);seen.add(key)
if __name__=='__main__':unittest.main()
