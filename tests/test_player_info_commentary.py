import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from player_info import enrich_picks, game_index, match_game  # noqa: E402


def test_lightweight_player_refresh_rebuilds_only_player_commentary(tmp_path):
    base = "시장 기본값은 주니치 승 59% · 배당 1.52. 주니치는 최근 흐름이 좋아지고 있다."
    picks_path = tmp_path / "picks.json"
    picks_path.write_text(json.dumps({
        "live": [{
            "sport": "bs", "league": "NPB", "date": "08.23(일) 14:00",
            "home": "주니치", "away": "야쿠르트",
            "추천": {"모델확률": .59}, "해설": base,
            "선발": {},
        }],
        "past": [],
    }, ensure_ascii=False), encoding="utf-8")
    player_doc = {
        "generated_at": "2026-08-23T04:00:00+00:00",
        "games": [{
            "league": "NPB", "game_datetime": "2026-08-23T14:00:00+09:00",
            "home_team": "주니치", "away_team": "야쿠르트",
            "starters": {
                "home": {"name": "야나기 유야", "stats": {"era": 2.45}},
                "away": {"name": "마츠모토 켄고", "stats": {"era": 3.61}},
            },
            "lineups": {
                "home": [{"order": 3, "name": "보슬러", "stats": {"ops": .842}}],
                "away": [{"order": 4, "name": "무라카미 무네타카", "stats": {"ops": .910}}],
            },
            "lineup_status": {"state": "official_today"},
        }],
    }

    assert enrich_picks(player_doc, picks_path) == 1
    game = json.loads(picks_path.read_text(encoding="utf-8"))["live"][0]
    assert game["해설기본"] == base
    assert game["추천"]["모델확률"] == .59
    assert "선발 맞대결은 주니치 야나기 유야(ERA 2.45)" in game["해설"]
    assert "오늘 공식 타순의 팀별 OPS 상위 타자" in game["해설"]


def test_game_matching_keeps_year_and_doubleheader_kickoff_distinct():
    doc = {"games": [
        {"league": "KBO", "game_datetime": "2025-08-23T14:00:00+09:00",
         "home_team": "홈", "away_team": "원정",
         "starters": {"home": {"name": "작년"}}},
        {"league": "KBO", "game_datetime": "2026-08-23T14:00:00+09:00",
         "home_team": "홈", "away_team": "원정",
         "starters": {"home": {"name": "더블1"}}},
        {"league": "KBO", "game_datetime": "2026-08-23T18:00:00+09:00",
         "home_team": "홈", "away_team": "원정",
         "starters": {"home": {"name": "더블2"}}},
    ]}
    index = game_index(doc)
    reference = datetime(2026, 8, 23, tzinfo=ZoneInfo("Asia/Seoul"))

    first = match_game(index, "KBO", "08.23(일) 14:00", "홈", "원정", reference)
    second = match_game(index, "KBO", "08.23(일) 18:00", "홈", "원정", reference)

    assert first["home"] == "더블1"
    assert second["home"] == "더블2"


def test_lightweight_refresh_does_not_rewrite_past_with_future_stats(tmp_path):
    picks_path = tmp_path / "picks.json"
    original = {"home": "당시 선발", "home_detail": {"stats": {"era": 2.0}}}
    picks_path.write_text(json.dumps({"live": [], "past": [{
        "sport": "bs", "league": "KBO", "date": "08.01(토) 18:00",
        "home": "홈", "away": "원정", "선발": original,
    }]}, ensure_ascii=False), encoding="utf-8")
    player_doc = {"generated_at": "2026-08-23T00:00:00Z", "games": []}

    enrich_picks(player_doc, picks_path)

    past = json.loads(picks_path.read_text(encoding="utf-8"))["past"][0]
    assert past["선발"] == original
