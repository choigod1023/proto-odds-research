import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wisetoto import repair_mojibake_segments, repair_text_tree  # noqa: E402


def test_mixed_korean_commentary_repairs_only_corrupted_team_token():
    source = "시장 기본값은 нЕНмВђл†ИмЭЄ 승 60%입니다."
    assert repair_mojibake_segments(source) == "시장 기본값은 텍사레인 승 60%입니다."


def test_real_accented_player_name_is_preserved():
    assert repair_mojibake_segments("Anrijs Miška") == "Anrijs Miška"


def test_json_tree_repair_keeps_non_text_values():
    value = {"home": "LAмЧРмЭЄм†И", "odds": 1.5, "rows": ["정상", None]}
    assert repair_text_tree(value) == {
        "home": "LA에인절", "odds": 1.5, "rows": ["정상", None]}
