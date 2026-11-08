import importlib.util
from pathlib import Path
import pytest
p=Path(__file__).resolve().parents[1]/'experiments/volleyball-source-20261108d/kovo_collect.py';spec=importlib.util.spec_from_file_location('kovo_source',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://dbbank.kovo.co.kr/game/11110_pop_result.asp?PID=gIzMxoTMwIjOxIDM'
def fixture():
 total='<tr><td colspan="9">합계</td>'+''.join(f'<td>{x}</td>' for x in ['77','54','10','8','5','35','','35.56','17.22','','37','28.12'])+'</tr>'
 players='<table><tr><td><a href="/club/player.asp?pcode=42">x</a></td></tr>'+total+'</table>'
 return ('<h3>2025.01.09 (목)</h3><table><tr><th>SETS</th></tr><tr><td>2</td><td>A</td><td>19</td><td>25</td><td>25</td><td>22</td><td>14</td><td>105</td><td>3</td></tr><tr><td>B</td><td>25</td><td>23</td><td>17</td><td>25</td><td>16</td><td>106</td></tr></table><table><tr><th>경기번호</th><td>132</td></tr><tr><th>시작시간</th><td>19:00</td><th>종료시간</th><td>21:26</td></tr></table>'+players+players).encode()
def test_score_and_team_field_positions():
 x=m.parse(fixture(),URL);assert x['official_game_number']==132 and x['set_points'][0]==(19,25) and x['sets_won']==[2,3];t=x['team_totals'][0];assert t['block_points']=='8' and t['player_errors']=='35' and t['receive_efficiency_pct']=='28.12';assert x['player_lists'][0]['player_ids']==['42']
def test_wrong_identity_and_corrupted_score_rejected():
 with pytest.raises(ValueError):m.parse(fixture().replace(b'<td>132</td>',b'<td>133</td>'),URL)
 with pytest.raises(ValueError):m.parse(fixture().replace(b'<td>105</td>',b'<td>100</td>'),URL)
 with pytest.raises(ValueError):m.parse(fixture(),'https://example.com/game/11110_pop_result.asp?PID=gIzMxoTMwIjOxIDM')
