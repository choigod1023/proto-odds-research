import sys
from pathlib import Path
sys.path.insert(0,'experiments/npb-recovery-20261102')
from official_2026 import parse_2026, daily_pairs_2026
from continuation_recover import index_complete_2026, pitcher_links_2023

BOX='''<html><body>2026年7月31日 試合終了 ◇開始18:00
<table><tr><th></th><th>1</th><th>計</th><th>H</th></tr><tr><td>客</td><td>2</td><td>2</td><td>3</td></tr><tr><td>主</td><td>1</td><td>1</td><td>2</td></tr></table>
<h3>東北楽天ゴールデンイーグルス</h3><table id="tablefix_t_p"><tr><th>投手</th><th>打者</th></tr><tr><td><a href="/bis/players/123.html">投手A</a></td><td>20</td></tr></table>
<h3>北海道日本ハムファイターズ</h3><table id="tablefix_b_p"><tr><th>投手</th><th>打者</th></tr><tr><td><a href="/bis/players/456.html">投手B</a></td><td>25</td></tr></table></body></html>'''.encode()
URL='https://npb.jp/scores/2026/0731/f-e-01/box.html'

def test_official_player_ids_require_team_date_and_first_pitcher():
 g=parse_2026(BOX,URL,'2026-07-31')
 assert (g['home_starter_player']['player_id'],g['away_starter_player']['player_id'])==('456','123')
 assert (g['home_score'],g['away_score'])==(1,2)
 assert parse_2026(BOX,URL,'2026-07-30') is None
 assert parse_2026(BOX.replace('北海道日本ハムファイターズ'.encode(),'中日ドラゴンズ'.encode()),URL,'2026-07-31') is None
 assert parse_2026(BOX.replace(b'/bis/players/456.html',b'/other/456.html'),URL,'2026-07-31') is None
 broken=BOX.replace(b'/bis/players/456.html',b'/other/456.html').replace(b'</td><td>25</td></tr>',b'</td><td>25</td></tr><tr><td><a href="/bis/players/789.html">relief</a></td><td>4</td></tr>')
 assert parse_2026(broken,URL,'2026-07-31') is None

def test_2026_doubleheader_and_index_detection():
 a='<a href="/scores/2026/0731/f-e-01/">first</a>'
 b='<a href="/scores/2026/0731/f-e-02/">second</a>'
 pairs=daily_pairs_2026((a+b).encode(),'2026-07-31')
 assert index_complete_2026((a+b).encode(),'2026-07-31') and len(pairs)==2 and pairs[0][1]==pairs[1][1]
 assert not index_complete_2026((a+a).encode(),'2026-07-31')

def test_historical_starter_cells_have_no_profile_links():
 p=next(Path('experiments/npb-recovery-20261102/raw').glob('07ca*.html'))
 assert pitcher_links_2023(p.read_bytes())==[None,None]

def test_full_cohort_preserves_prior_and_labels_receipt():
 import json
 d=Path('experiments/npb-recovery-20261102'); rows=[json.loads(x) for x in (d/'cohort-continuation.jsonl').read_text(encoding='utf8').splitlines()]; old=[json.loads(x) for x in (d/'cohort-expanded.jsonl').read_text(encoding='utf8').splitlines()]
 assert len(rows)==935 and len({x['game_id_string'] for x in rows})==935
 prior={x['game_id_string'] for x in old if x['status']=='retrospective_names_recovered'}; restored={x['game_id_string'] for x in rows if x['status']=='retrospective_names_recovered'}
 assert len(prior)==256 and prior<=restored
 assert all(x['actual_pregame_receipt']=='not_verified' and x['same_price_product_time']=='not_checked' for x in rows)
 for x in rows:
  if x['status']=='retrospective_names_recovered':
   assert x['source_observed_at'][:10] > x['date']
   for side in ('home','away'):
    ident=x['official_player_id_'+side]; player=x.get(side+'_starter_player')
    if ident:
     assert player['role']=='starter' and player['player_id']==ident and player['player_url'].endswith('/'+ident+'.html')

def test_new_network_budget_and_status_partition():
 import json
 d=Path('experiments/npb-recovery-20261102'); m=json.loads((d/'manifest-continuation.json').read_text(encoding='utf8'))
 assert sum(m['statuses'].values())==935 and m['new_network_attempts']<=1000
 assert m['actual_pregame_receipt_recovered']==0 and m['same_price_product_time'] is None

def test_cancelled_and_mismatched_start_are_preserved_as_exclusions():
 import json
 rows=[json.loads(x) for x in Path('experiments/npb-recovery-20261102/cohort-continuation.jsonl').read_text(encoding='utf8').splitlines()]
 cancels=[x for x in rows if x['status']=='official_cancelled']; ambiguous=[x for x in rows if x['status']=='start_time_diff_gt_30m']
 assert len(cancels)==2 and {x['date'] for x in cancels}=={'2026-06-08','2026-07-31'}
 assert len(ambiguous)==2 and sorted(x['start_delta_seconds'] for x in ambiguous)==[2340,2760]
 assert all(x['candidate_score_matches'] and x['candidate_home_starter_name'] and x['candidate_away_starter_name'] for x in ambiguous)
