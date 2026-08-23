from datetime import datetime
from zoneinfo import ZoneInfo

from src.japan_info import (jleague_record_for, parse_jleague_standings,
                            parse_npb_standings, parse_npb_starters)


KST = ZoneInfo("Asia/Seoul")


def test_parse_npb_official_starters_maps_teams_and_players():
    html = """
    <div class="contents"><h4>8月23日の予告先発投手</h4>
      <section class="starting_wrap_cl"><div class="unit">
        <div class="team_left"><img alt="読売ジャイアンツ">
          <a href="/bis/players/111.html"><span>小笠原　慎之介</span></a></div>
        <div class="team_right"><img alt="広島東洋カープ">
          <a href="/bis/players/222.html"><span>森　翔平</span></a></div>
        <div class="info">（東京ドーム）14:00</div>
      </div></section>
    </div>"""
    games = parse_npb_starters(html, datetime(2026, 8, 23, 1, tzinfo=KST))
    assert len(games) == 1
    assert games[0]["home_team"] == "요미우리"
    assert games[0]["away_team"] == "히로시마"
    assert games[0]["starters"]["home"]["name"] == "小笠原 慎之介"
    assert games[0]["starters"]["away"]["player_id"] == "222"
    assert games[0]["game_datetime"] == "2026-08-23T14:00:00+09:00"


def test_parse_npb_official_standings_keeps_rank_and_draws():
    html = """
    <table><tr class="ststats"><td>阪神タイガース</td><td>110</td>
      <td>62</td><td>47</td><td>1</td><td>.569</td><td>--</td></tr>
    <tr class="ststats"><td>読売ジャイアンツ</td><td>112</td>
      <td>60</td><td>50</td><td>2</td><td>.545</td><td>2.5</td></tr></table>"""
    table = parse_npb_standings(html)
    assert table["한신"] == {
        "rank": 1, "played": 110, "wins": 62, "losses": 47, "draws": 1,
        "pct": .569, "games_behind": None,
    }
    assert table["요미우리"]["rank"] == 2
    assert table["요미우리"]["games_behind"] == 2.5


def test_parse_jleague_official_standings_uses_proto_abbreviation():
    html = """
    <table><tr class="o-table__row">
      <td class="o-table__cell--ranking">4</td>
      <td class="o-table__cell--club"><a>ＦＣ町田ゼルビア</a></td>
      <td class="o-table__cell--point">6</td><td class="o-table__cell--match">2</td>
      <td class="o-table__cell--win">2</td><td class="o-table__cell--draw">0</td>
      <td class="o-table__cell--loss">0</td><td class="o-table__cell--goal-scored">9</td>
      <td class="o-table__cell--goal-lost">1</td>
      <td class="o-table__cell--past-games"><i class="o-table__game-state">W</i><i class="o-table__game-state">W</i></td>
    </tr></table>"""
    table = parse_jleague_standings(html)
    record = jleague_record_for(table, "마치다Z")
    assert record["rank"] == 4
    assert record["points"] == 6
    assert record["goals_per_game"] == 4.5
    assert record["last_five"] == "WW"
