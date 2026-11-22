import gzip,hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
D=Path(__file__).resolve().parents[1]/'experiments/kleague2-official-lineup-20261103/cohort'
def load(name):
 s=importlib.util.spec_from_file_location(name,D/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
collector=load('collect_cohort');derive=load('derive')
class TestCohort(unittest.TestCase):
 def test_lockstep_stop_and_cache_hash(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(collector,'RAW',Path(d)):
    class Resp:
     status_code=429
     def close(self):pass
    class Session:
     def request(self,*a,**k):return Resp()
    with self.assertRaisesRegex(RuntimeError,'429'):collector.capture(Session(),'case','https://example.test',{},'GET')
    self.assertEqual(list(Path(d).iterdir()),[])
    body=b'official original\r\n'; comp=gzip.compress(body,mtime=0)
    (Path(d)/'case.gz').write_bytes(comp)
    (Path(d)/'case.json').write_text(json.dumps({'stored_sha256':collector.sha(comp),'source_sha256':collector.sha(body)}),encoding='utf8')
    self.assertEqual(collector.read_capture('case'),body)
    (Path(d)/'case.gz').write_bytes(gzip.compress(b'tampered',mtime=0))
    with self.assertRaisesRegex(RuntimeError,'hash mismatch'):collector.read_capture('case')
 def test_real_match_and_wrong_fixture(self):
  path=D/'census-2026.json'
  if not path.exists():self.skipTest('cohort not captured yet')
  fixtures=json.loads(path.read_bytes())['fixtures'];first=next(x for x in fixtures if x['gameId']==1)
  x=derive.parse_match(first)
  self.assertEqual([len(t['starting_eleven']) for t in x['teams']],[11,11]);self.assertEqual(x['lineup_status'],'retrospective_official_actual_starting_eleven');self.assertFalse(x['pregame_announcement_confirmed'])
  bad=dict(first,homeTeam='K999')
  with self.assertRaisesRegex(ValueError,'team mismatch'):derive.parse_match(bad)
  bad=dict(first,gameDate='2026.03.01')
  with self.assertRaisesRegex(ValueError,'date mismatch'):derive.parse_match(bad)
 def test_each_season_census_unique(self):
  for year in (2026,2025):
   p=D/f'census-{year}.json'
   if p.exists():
    x=json.loads(p.read_bytes())['fixtures'];ids=[y['gameId'] for y in x]
    self.assertEqual(len(ids),len(set(ids)));self.assertTrue(all(y['meetSeq']==2 and y['leagueId']==2 and y['year']==year for y in x))
 def test_price_bridge_has_no_invented_ids_or_receipts(self):
  path=D/'price-bridge.json'
  if not path.exists():self.skipTest('bridge not derived yet')
  rows=json.loads(path.read_bytes())
  self.assertEqual(len({x['target_event_id'] for x in rows}),len(rows))
  self.assertTrue(all(x['prediction_ids'] is None and not x['source_receipt_certified'] for x in rows))
  self.assertTrue(all(x['direct_event_id_agrees'] and x['date_equal'] and x['kickoff_equal'] and x['team_names_exact'] and x['score_outcome_equal'] and x['complete_3way_odds'] for x in rows))
if __name__=='__main__':unittest.main()
