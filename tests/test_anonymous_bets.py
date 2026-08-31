import json

import pytest

from deploy.supervisor import store_anonymous_bet, validate_anonymous_bet


def sample():
    return {"schema_version": 1, "source": "receipt_ocr", "round": 102,
            "combo_size": 1, "combined_odds": 1.43, "stake_band": "10000_49999",
            "legs": [{"game_no": "7830", "sport": "sc", "league": "K리그1",
                      "market": "승무패", "label": "", "choice": "원정", "purchase_odds": 1.43}]}


def test_only_anonymous_fields_are_stored(tmp_path):
    value = sample() | {"purchase_number": "secret", "image": "raw", "ip": "127.0.0.1"}
    stored = store_anonymous_bet(value, tmp_path / "bets.jsonl")
    assert "purchase_number" not in stored
    assert "image" not in stored
    assert "ip" not in stored
    assert json.loads((tmp_path / "bets.jsonl").read_text())["legs"][0]["game_no"] == "7830"


def test_invalid_values_are_rejected():
    value = sample()
    value["legs"][0]["game_no"] = "not-a-game"
    with pytest.raises(ValueError):
        validate_anonymous_bet(value)
