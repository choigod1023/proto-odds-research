import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generate_v2 import _attach_story  # noqa: E402


def test_unpublished_odds_never_create_a_fake_fifty_fifty_market_story():
    game = {
        "year": 2026,
        "round": 101,
        "date": "08.27(목) 19:00",
        "league": "KBO",
        "sport": "bs",
        "home": "서울",
        "away": "부산",
        "options": [],
    }

    _attach_story(game, {}, {}, {})

    assert "시장 방향과 확률은 정하지 않는다" in game["해설"]
    for fake_market_phrase in ("50%", "반반", "낮은 배당", "시장 기본값"):
        assert fake_market_phrase not in game["해설"]
