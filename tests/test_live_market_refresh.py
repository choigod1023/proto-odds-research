from src.live_market_refresh import refresh_document


def _live_odds():
    return {
        "generated_at": "2026-08-30T01:00:00+00:00",
        "markets": {"102": {
            "7100": {
                "game_no": "7100", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "승패", "label": "", "n_way": 2,
                "odds": [1.55, 2.05], "result": "경기전",
            },
            "7101": {
                "game_no": "7101", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "언더오버", "label": "U 10.5", "n_way": 2,
                "odds": [1.70, 1.82], "result": "경기전",
            },
        }},
    }


def test_refresh_hydrates_unpriced_game_and_updates_document_clock():
    document = {"generated_at": "2026-08-28T00:00:00+00:00", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    game = refreshed["live"][0]
    assert changed == 1
    assert refreshed["generated_at"] == _live_odds()["generated_at"]
    assert game["status"] == "경기전"
    assert len(game["options"]) == 4
    assert game["decision_snapshot"]["action"] == "market_reference"
    assert game["decision_snapshot"]["probability"]["market"] is not None


def test_refresh_adds_new_round_game_without_structure_model():
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    assert changed == 1
    assert refreshed["rounds"] == [102]
    assert refreshed["live"][0]["no_model"] is True
    assert refreshed["live"][0]["추천"] is not None
