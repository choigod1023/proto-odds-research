import hashlib,importlib.util,json,shutil,tempfile,unittest
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]/'experiments/kleague2-official-lineup-20261103'
spec=importlib.util.spec_from_file_location('k2_parse',ROOT/'parse.py'); parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)

class TestOfficialK2(unittest.TestCase):
 def test_captured_samples(self):
  for year,league,game in parser.SAMPLES:
   x=parser.parse(year,league,game)
   self.assertEqual([len(s['starters']) for s in x['sides']],[11,11])
   self.assertTrue(all(len({p['player_id'] for p in s['starters']+s['bench']})==len(s['starters'])+len(s['bench']) for s in x['sides']))
   self.assertIsNone(x['announcement_time'])
 def test_tampering_and_wrong_page_identity_rejected(self):
  old=parser.ROOT
  with tempfile.TemporaryDirectory() as tmp:
   parser.ROOT=Path(tmp); dest=parser.ROOT/'raw';dest.mkdir()
   try:
    for p in (old/'raw').glob('2025-2-1-*'):shutil.copyfile(p,dest/p.name)
    raw=dest/'2025-2-1-match.bin';body=raw.read_bytes();raw.write_bytes(body+b' ')
    with self.assertRaisesRegex(ValueError,'hash mismatch'):parser.parse(2025,2,1)
    # Rewrite both bytes and hash: the selected match identity must still be checked.
    soup=BeautifulSoup(body.decode('utf8'),'html.parser')
    soup.select_one('#gameId option[selected]')['value']='999'
    changed=str(soup).encode('utf8'); raw.write_bytes(changed)
    meta=dest/'2025-2-1-match.json';x=json.loads(meta.read_text(encoding='utf8'));x['sha256']=hashlib.sha256(changed).hexdigest();meta.write_text(json.dumps(x),encoding='utf8')
    with self.assertRaisesRegex(ValueError,'page identity'):parser.parse(2025,2,1)
   finally:parser.ROOT=old
 def test_independent_schedule_and_result_joins(self):
  x=json.loads((ROOT/'summary.json').read_text(encoding='utf8'))
  self.assertEqual(len(x['official_2025_result_matches']),2)
  self.assertEqual(len(x['official_other_season_schedule_matches']),2)
  for row in x['official_2025_result_matches']+x['official_other_season_schedule_matches']:
   self.assertTrue(row['team_ids_agree'] and row['kickoff_date_agree'] and row['score_agrees'])
  self.assertEqual(x['review_targets_matching_event_id'],0)
  self.assertEqual(x['substitution_rows_partner_id_unresolved'],2)

if __name__=='__main__':unittest.main()
