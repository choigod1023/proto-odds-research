import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from context_features import match_context, team_profile  # noqa: E402


def history():
    rows = []
    for day in range(1, 7):
        rows.append({"date": f"2026-08-{day:02d}", "year": 2026, "league": "L",
                     "home_team": "A" if day % 2 else "B", "away_team": "B" if day % 2 else "A",
                     "home_score": 3 if day % 2 else 1, "away_score": 1 if day % 2 else 2})
    return pd.DataFrame(rows)


def test_profile_uses_only_past_and_shrinks_small_samples():
    early = team_profile(history(), "L", "A", pd.Timestamp("2026-08-03"), prior_games=8)
    late = team_profile(history(), "L", "A", pd.Timestamp("2026-08-07"), prior_games=8)
    assert early.games == 2
    assert late.games == 6
    assert early.reliability < late.reliability < 1
    assert early.rest_days == 1


def test_context_degrades_gracefully_for_exceptions():
    fixture = {"date": "2026-08-08", "year": 2026, "league": "L",
               "home_team": "A", "away_team": "NEW", "neutral_venue": True,
               "competition_stage": "knockout", "lineup_status": "projected"}
    context = match_context(history(), fixture, data_updated_at=pd.Timestamp("2026-08-05"))
    assert context["application_mode"] == "market_only"
    assert {"low_season_sample", "neutral_venue", "non_regular_stage",
            "lineup_unconfirmed", "stale_context_data"}.issubset(context["exceptions"])
    assert context["differences"]["score_margin"] is None
    assert "요일 자체" in context["rule"]
