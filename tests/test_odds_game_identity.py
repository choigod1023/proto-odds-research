import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from matches import load_matches  # noqa: E402
from model_v2 import attach_odds  # noqa: E402


def raw_row(*, time: str, score: tuple[int, int], odds: str, round_no: int,
            date: str = "04.01", year: int = 2026, league: str = "KBO") -> dict:
    home_score, away_score = score
    return {
        "year": year, "round": round_no, "game_no": "1",
        "date_text": f"{date}(수) {time}", "sport": "bs", "league": league,
        "market_family": "승패", "n_way": 2,
        "home": f"홈 {home_score}", "away": f"{away_score} 원정",
        "odds": odds, "result": "홈승" if home_score > away_score else "홈패",
        "is_void": False,
    }


def test_load_matches_preserves_doubleheader_times_but_deduplicates_resales(tmp_path):
    path = tmp_path / "games.csv"
    rows = [
        raw_row(time="14:00", score=(3, 1), odds="1.50,2.20", round_no=1),
        raw_row(time="14:00", score=(3, 1), odds="1.50,2.20", round_no=2),
        raw_row(time="18:00", score=(1, 4), odds="1.60,2.10", round_no=2),
    ]
    pd.DataFrame(rows).to_csv(path, index=False)

    matches = load_matches(path=path)

    assert len(matches) == 2
    assert matches["date"].dt.hour.tolist() == [14, 18]


def test_attach_odds_excludes_conflicting_resale_prices(tmp_path):
    path = tmp_path / "games.csv"
    rows = [
        raw_row(time="14:00", score=(3, 1), odds="1.50,2.20", round_no=1),
        raw_row(time="14:00", score=(3, 1), odds="1.55,2.10", round_no=2),
        raw_row(time="18:00", score=(1, 4), odds="1.60,2.10", round_no=2),
        raw_row(time="18:00", score=(1, 4), odds="1.60,2.10", round_no=3),
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-04-01 14:00", "2026-04-01 18:00"]),
        "league": ["KBO", "KBO"], "home_team": ["홈", "홈"],
        "away_team": ["원정", "원정"], "outcome": [1.0, 0.0],
    })

    joined = attach_odds(frame, path)

    assert len(joined) == 1
    assert joined.iloc[0]["date"] == pd.Timestamp("2026-04-01 18:00")
    assert joined.iloc[0]["o_home"] == 1.60


def test_round_one_december_game_is_assigned_to_previous_calendar_year(tmp_path):
    path = tmp_path / "games.csv"
    pd.DataFrame([
        raw_row(time="21:30", score=(80, 75), odds="1.70,1.90", round_no=1,
                date="12.31", year=2026, league="KBL"),
    ]).to_csv(path, index=False)

    matches = load_matches(path=path)
    assert matches.iloc[0]["date"] == pd.Timestamp("2025-12-31 21:30")
    assert matches.iloc[0]["year"] == 2025

    joined = attach_odds(matches, path)
    assert len(joined) == 1
    assert joined.iloc[0]["date"] == pd.Timestamp("2025-12-31 21:30")
