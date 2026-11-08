import json, gzip, unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'experiments/nba-lineup-rapm-20261108'
class AuditTests(unittest.TestCase):
 def test_scores_and_unique_event_keys(self):
  audit=json.loads((P/'event-audit.json').read_text(encoding='utf8'))
  self.assertEqual(len(audit),5)
  for item in audit.values():
   self.assertEqual(item['last_score'],[item['official_result_payload']['home_score'],item['official_result_payload']['away_score']])
   self.assertFalse(item['start5_available'])
  seen=set()
  with gzip.open(P/'events.jsonl.gz','rt',encoding='utf8') as f:
   for line in f:
    r=json.loads(line); key=(r['official_game_id'],r['event']['actionNumber'])
    self.assertNotIn(key,seen); seen.add(key)
  self.assertEqual(len(seen),sum(x['events'] for x in audit.values()))
 def test_price_and_pregame_gates(self):
  x=json.loads((P/'summary.json').read_text(encoding='utf8'))
  self.assertEqual(x['eligible_priced_games'],0)
  self.assertEqual(x['prematch_model_evaluations'],0)
  self.assertIsNone(x['roi'])
if __name__=='__main__': unittest.main()
