import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/kbo-source-model-20261108d/verify_join.py';s=importlib.util.spec_from_file_location('kbo_source_verify',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class KboSourceJoinTest(unittest.TestCase):
 def test_actual_recorded_responses(self):
  if not (m.EVIDENCE/'player.bin').exists():self.skipTest('local retained response absent')
  p=m.parse_player((m.EVIDENCE/'player.bin').read_bytes());g=m.parse_game((m.EVIDENCE/'game.bin').read_bytes());self.assertEqual(p['official_player_id'],'50030');self.assertEqual(set(p['season_rows']),{'2025','2026'});self.assertEqual(g['pitcher']['pitch_count'],84);self.assertEqual(g['pitching_table_row_counts'],[4,7])
 def test_preserved_official_bundle_replays(self):
  import json
  path=m.STATE/'paired-ledger.jsonl'
  if not path.exists():self.skipTest('local retained official archive absent')
  capture=next(r for r in (json.loads(line) for line in path.read_text(encoding='utf8').splitlines()) if r.get('type')=='capture' and r['input']['event']['official_game_id']=='20261106KTSS0')
  self.assertIsNone(m._verify_archives(capture['input'],m.STATE/'official',m.datetime.fromisoformat(capture['frozen_at'])))
 def test_refuses_ambiguous_official_player_link(self):
  html=b'<a href="https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId=12">one</a><a href="https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId=13">two</a>'
  with self.assertRaisesRegex(ValueError,'ambiguous'):m.parse_player(html)
 def test_refuses_body_hash_mismatch(self):
  path=m.EVIDENCE/'player.bin'
  if not path.exists():self.skipTest('local retained response absent')
  with self.assertRaisesRegex(ValueError,'integrity'):m.read_verified(path,{'sha256':'0'*64,'bytes':path.stat().st_size,'status':200})
if __name__=='__main__':unittest.main()
