from src.game_dedup import deduplicate_game_sections


def test_keeps_priced_latest_reissue_and_removes_waiting_round():
    base = {"sport": "bk", "league": "남농월예", "date": "08.31(월) 19:00",
            "home": "한국M", "away": "사우디M"}
    document = {"live": [
        {**base, "round": 102, "status": "배당대기", "options": []},
        {**base, "round": 103, "status": "경기전", "options": [{"배당": 1.75}]},
    ], "past": []}

    assert deduplicate_game_sections(document) == 1
    assert [game["round"] for game in document["live"]] == [103]


def test_keeps_real_doubleheader_with_different_start_times():
    base = {"sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드"}
    document = {"live": [], "past": [
        {**base, "date": "08.30(일) 02:05", "round": 102},
        {**base, "date": "08.30(일) 08:15", "round": 103},
    ]}

    assert deduplicate_game_sections(document) == 0
    assert len(document["past"]) == 2
