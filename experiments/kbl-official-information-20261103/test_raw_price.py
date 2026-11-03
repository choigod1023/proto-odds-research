import datetime as dt,gzip,hashlib,json,unittest
from pathlib import Path
P=Path(__file__).resolve().parent
class RawPriceTests(unittest.TestCase):
 def test_joined_official_bytes(self):
  out=json.loads((P/'price-join.json').read_text(encoding='utf8')); c=json.loads((P/'census.json').read_text(encoding='utf8'));fixtures={x['gmkey']:x for x in c['regular_season_fixtures']}
  for r in out['rows']:
   zipped=(P/r['source_file']).read_bytes(); raw=gzip.decompress(zipped)
   self.assertEqual(hashlib.sha256(raw).hexdigest(),r['betman_source_sha256']);self.assertEqual(hashlib.sha256(zipped).hexdigest(),r['compressed_sha256'])
   d=json.loads(raw);cs=d['compSchedules']; hits=[dict(zip(cs['keys'],v)) for v in cs['datas'] if str(dict(zip(cs['keys'],v)).get('matchSeq'))==str(r['match_seq'])]
   self.assertEqual(len(hits),1);a=hits[0];f=fixtures[r['gmkey']];t=dt.datetime.fromtimestamp(a['gameDate']/1000,dt.timezone(dt.timedelta(hours=9)))
   self.assertEqual((t.strftime('%Y%m%d'),t.strftime('%H%M'),a['homeId'],a['awayId']),(f['gameDate'],f['gameStart'],f['tcodeH'],f['tcodeA']))
   self.assertEqual((a['winAllot'],a['loseAllot']),(r['home_odds'],r['away_odds']))
   self.assertIsNone(r['price_observed_at'])
if __name__=='__main__':unittest.main()
