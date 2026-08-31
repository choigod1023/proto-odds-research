from src.player_name_localizer import localize_player_names


def test_prefers_cached_korean_name_and_preserves_native_name():
    records = [{"key_players": {"home": [
        {"player_id": "1", "name": "Jalen Harris", "position": "Guard"},
    ]}}]
    result = localize_player_names(records, api_key="", cache={"Jalen Harris": "제일런 해리스"})
    player = records[0]["key_players"]["home"][0]
    assert player["name"] == "제일런 해리스"
    assert player["name_ko"] == "제일런 해리스"
    assert player["native_name"] == "Jalen Harris"
    assert result["localized"] == 1


def test_keeps_original_when_translation_is_unavailable():
    records = [{"lineups": {"away": [
        {"player_id": "2", "name": "Unknown Player", "position": "Forward"},
    ]}}]
    result = localize_player_names(records, api_key="", cache={})
    player = records[0]["lineups"]["away"][0]
    assert player["name"] == "Unknown Player"
    assert "native_name" not in player
    assert result["pending"] == 1


def test_uses_supplied_korean_name_without_api_call():
    records = [{"starters": {"home": {
        "player_code": "3", "name": "大谷翔平", "name_ko": "오타니 쇼헤이",
    }}}]
    localize_player_names(records, api_key="", cache={})
    player = records[0]["starters"]["home"]
    assert player["name"] == "오타니 쇼헤이"
    assert player["native_name"] == "大谷翔平"
