from datetime import date, datetime

from src.npb_lineups import (collect_npb_official_lineups,
                             find_npb_player_stats, parse_npb_batting_stats, parse_npb_box_lineups,
                             parse_npb_daily_box_links, parse_npb_pitching_stats)


TEAM_KO = {
    "広島東洋カープ": "히로시마",
    "読売ジャイアンツ": "요미우리",
}


def test_daily_page_keeps_only_requested_dates_score_links():
    html = """
    <a href="/scores/2026/0823/g-c-17/">오늘</a>
    <a href="/scores/2026/0822/g-c-16/">어제 경기</a>
    <a href="https://npb.jp/scores/2026/0822/d-s-18/">어제 경기2</a>
    """
    links = parse_npb_daily_box_links(html, date(2026, 8, 22))
    assert links == [
        "https://npb.jp/scores/2026/0822/g-c-16/box.html",
        "https://npb.jp/scores/2026/0822/d-s-18/box.html",
    ]


def test_box_score_uses_numbered_starters_and_skips_substitutes():
    html = """
    <h4>広島東洋カープ</h4><table>
      <tr><th></th><th>守備</th><th>選手</th><th>打数</th><th>得点</th>
        <th>安打</th><th>打点</th><th>盗塁</th></tr>
      <tr><td>1</td><td>(中)</td><td><a href="/bis/players/111.html">秋山</a></td>
        <td>5</td><td>0</td><td>2</td><td>0</td><td>0</td></tr>
      <tr><td>2</td><td>(二)</td><td><a href="/bis/players/222.html">菊池</a></td>
        <td>4</td><td>1</td><td>1</td><td>1</td><td>0</td></tr>
      <tr><td></td><td>打</td><td><a href="/bis/players/333.html">代打</a></td>
        <td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>3</td><td>(一)</td><td>선수3</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>4</td><td>(三)</td><td>선수4</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>5</td><td>(遊)</td><td>선수5</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>6</td><td>(左)</td><td>선수6</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>7</td><td>(右)</td><td>선수7</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>8</td><td>(捕)</td><td>선수8</td><td>4</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
      <tr><td>9</td><td>(投)</td><td>선수9</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
    </table>
    """
    players = parse_npb_box_lineups(html, TEAM_KO)["히로시마"]
    assert len(players) == 9
    assert players[0]["name"] == "秋山"
    assert players[0]["position"] == "중견수"
    assert players[0]["last_game"]["hits"] == 2
    assert all(player["name"] != "代打" for player in players)
    assert players[-1]["position"] == "투수"


def test_team_batting_page_maps_registered_name_to_season_metrics():
    html = """
    <table class="tablefix2"><tr><th>選手</th></tr><tr>
      <td>*秋山　翔吾</td>
      <td>99</td><td>410</td><td>368</td><td>37</td><td>95</td><td>22</td>
      <td>0</td><td>5</td><td>132</td><td>38</td><td>3</td><td>1</td>
      <td>0</td><td>0</td><td>41</td><td>0</td><td>1</td><td>57</td>
      <td>8</td><td>.258</td><td>.359</td><td>.334</td>
    </tr></table>
    """
    stats = parse_npb_batting_stats(html, 2026)["秋山翔吾"]
    assert stats["games"] == 99
    assert stats["avg"] == .258
    assert stats["home_runs"] == 5
    assert stats["rbi"] == 38
    assert stats["ops"] == .693


def test_team_pitching_page_maps_official_season_metrics():
    html = """
    <table class="tablefix2"><tr><th>選手</th></tr><tr>
      <td>*松本　晴</td><td>20</td><td>8</td><td>4</td><td>0</td><td>0</td>
      <td>0</td><td>1</td><td>1</td><td>0</td><td>.667</td><td>410</td>
      <td>100 .2</td><td>80</td><td>10</td><td>20</td><td>0</td><td>2</td>
      <td>90</td><td>1</td><td>0</td><td>30</td><td>28</td><td>2.50</td>
    </tr></table>
    """
    stats = parse_npb_pitching_stats(html, 2026)["松本晴"]
    assert stats["record"] == "8승 4패"
    assert stats["innings_display"] == "100⅔"
    assert stats["era"] == 2.5
    assert stats["whip"] == .99
    assert stats["k9"] == 8.05
    assert stats["strikeouts"] == 90
    assert find_npb_player_stats("Ｍ．松本 晴", {"松本晴": stats}) == stats


def test_today_box_score_becomes_official_lineup():
    rows = "".join(
        f"""<tr><td>{order}</td><td>(中)</td>
          <td><a href="/bis/players/{100 + order}.html">선수{order}</a></td>
          <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>"""
        for order in range(1, 10)
    )
    box = f"""
    <h4>広島東洋カープ</h4><table>
      <tr><th></th><th>守備</th><th>選手</th><th>打数</th><th>得点</th>
        <th>安打</th><th>打点</th><th>盗塁</th></tr>
      {rows}
    </table>
    """

    def fetch_html(_session, url):
        if "gm20260823" in url:
            return '<a href="/scores/2026/0823/g-c-17/">경기</a>'
        if url.endswith("/box.html"):
            return box
        return "<html></html>"

    official = collect_npb_official_lineups(
        object(), datetime(2026, 8, 23, 12), {"히로시마"}, TEAM_KO, fetch_html)
    assert len(official["히로시마"]["players"]) == 9
    assert official["히로시마"]["reference_date"] == "2026-08-23"
    assert official["히로시마"]["official_today"] is True
    assert official["히로시마"]["source_url"].endswith("/g-c-17/box.html")
