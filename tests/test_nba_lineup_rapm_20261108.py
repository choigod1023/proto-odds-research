import gzip, importlib.util, json, unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'experiments/nba-lineup-rapm-20261108'
spec=importlib.util.spec_from_file_location('nba_lineup_prototype',P/'prototype.py'); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
class AuditTests(unittest.TestCase):
 def test_five_official_scores_and_event_keys(self):
  audit=json.loads((P/'event-audit.json').read_text(encoding='utf8')); self.assertEqual(len(audit),5)
  for x in audit.values(): self.assertEqual(x['last_score'],[x['official_result_payload']['home_score'],x['official_result_payload']['away_score']])
  with gzip.open(P/'events.jsonl.gz','rt',encoding='utf8') as f: rows=[json.loads(x) for x in f]
  keys=[(r['official_game_id'],r['event']['actionNumber']) for r in rows]; self.assertEqual(len(keys),len(set(keys)))
 def test_prior_excludes_recent_and_current(self):
  def game(day,player):return {'start':f'2026-01-{day:02d}T00:00:00Z','end':f'2026-01-{day:02d}T03:00:00Z','home':'H','away':'A','stints':[{'h':[player],'a':['x'],'secs':10}]}
  past=[game(i,str(i)) for i in [1,2,3,4,5,9,10]]; target=game(10,'heldout')
  self.assertEqual(set(mod.recent(past,target,'H')),{(str(i),) for i in [1,2,3,4,5]})
  self.assertIsNone(mod.recent(past[:4],target,'H'))
 def test_actual_evaluation_and_no_price(self):
  x=json.loads((P/'prototype-summary.json').read_text(encoding='utf8')); self.assertEqual((x['train_games'],x['validation_scored'],x['test_scored']),(594,224,411)); self.assertEqual(x['price_matched_n'],0);self.assertIsNone(x['roi'])
  with gzip.open(P/'predictions.jsonl.gz','rt',encoding='utf8') as f: rows=[json.loads(x) for x in f]
  self.assertEqual(len(rows),635);self.assertEqual(len({r['id'] for r in rows}),635)
 def test_pair_feature_respects_side_and_ridge_scaling(self):
  a=('1','2','3','4','5');b=('6','7','8','9','10');d=mod.add_pairs({}, {a:1},{b:1});self.assertEqual(len(d),20); self.assertAlmostEqual(d['i:1:2'],-d['i:6:7'])
if __name__=='__main__':unittest.main()
