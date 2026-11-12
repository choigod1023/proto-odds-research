import importlib.util,json,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/kbo-pitching-evidence-20261108c/link_saved_pitch_counts.py';s=importlib.util.spec_from_file_location('kbo_pitch_link',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class PitchCountLinkTest(unittest.TestCase):
 def test_preserved_capture_replays_official_identity_and_box(self):
  path=m.STATE/'paired-ledger.jsonl'
  if not path.exists():self.skipTest('preserved local source unavailable')
  capture=next(r for r in (json.loads(x) for x in path.read_text(encoding='utf8').splitlines()) if r.get('type')=='capture' and r['input']['event']['official_game_id']=='20261106KTSS0')
  result=m.extract_capture(capture); rows=[r for r in result['rows'] if r['player_id']=='50030' and r['fixture_id']=='20261012KTSS02026']; self.assertEqual(len(rows),1);self.assertEqual(rows[0]['pitch_count'],84);self.assertEqual(rows[0]['box_sha256'],'6f28d97353ca799317a98243bee25bc5e75758e5561a39908faba6e4a2449459');self.assertEqual(result['excluded'].get('unresolved_identity'),1)
 def test_missing_pitch_count_column_is_not_fabricated(self):
  def table():return {'headers':[{'row':[{'Text':x} for x in ['선수명','이닝','피안타','홈런','삼진','실점','자책']]}],'rows':[]}
  box={'tables':[{}, {}, {},table(),table()]};g={'AWAY_ID':'A','HOME_ID':'H','AWAY_NM':'a','HOME_NM':'h','SEASON_ID':2026};n={'covered_player_teams':['A'],'pitchers':{}}
  with self.assertRaisesRegex(ValueError,'pitching_columns_missing_or_ambiguous'):m.rows_from_box(box,g,n,{},None,[])
 def test_unresolved_identity_never_creates_player_row(self):
  headers=['선수명','이닝','피안타','홈런','삼진','실점','자책','투구수']
  table={'headers':[{'row':[{'Text':x} for x in headers]}],'rows':[{'row':[{'Text':x} for x in ['동명이인','1','0','0','1','0','0','13']]}]}
  box={'tables':[{}, {}, {},table,table]};g={'AWAY_ID':'A','HOME_ID':'H','AWAY_NM':'a','HOME_NM':'h','SEASON_ID':2026};n={'covered_player_teams':['A'],'pitchers':{}}
  def unresolved(*args,**kwargs):raise m.official.SourceError('retired_last_season_unavailable')
  rows,reasons=m.rows_from_box(box,g,n,{},unresolved,[]);self.assertEqual(rows,[]);self.assertEqual(reasons,{'unresolved_identity':1})
 def test_exact_t30_rejected_without_reading(self):
  capture={'frozen_at':'2026-11-07T04:30:00+00:00','input':{'event':{'kickoff_at':'2026-11-07T05:00:00+00:00'}}}
  with self.assertRaisesRegex(ValueError,'capture_at_or_after_t30'):m.extract_capture(capture)
 def test_late_capture_rejected_without_reading(self):
  capture={'frozen_at':'2026-11-07T04:31:00+00:00','input':{'event':{'kickoff_at':'2026-11-07T05:00:00+00:00'}}}
  with self.assertRaisesRegex(ValueError,'capture_at_or_after_t30'):m.extract_capture(capture)
if __name__=='__main__':unittest.main()
