import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features import build_features, season_key  # noqa: E402
from pi_ratings import run_pi  # noqa: E402
from team_form import Form, build_forms, form_for_game  # noqa: E402


def sample(order: tuple[int, int]) -> pd.DataFrame:
    same_day = [
        {"date": "2026-01-01", "year": 2026, "league": "TEST", "sport": "bs",
         "home_team": "A", "away_team": "B", "home_score": 5, "away_score": 1,
         "outcome": 1.0},
        {"date": "2026-01-01", "year": 2026, "league": "TEST", "sport": "bs",
         "home_team": "A", "away_team": "C", "home_score": 1, "away_score": 4,
         "outcome": 0.0},
    ]
    rows = [same_day[i] for i in order]
    rows.append(
        {"date": "2026-01-02", "year": 2026, "league": "TEST", "sport": "bs",
         "home_team": "A", "away_team": "D", "home_score": 3, "away_score": 2,
         "outcome": 1.0}
    )
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def canonical(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)


def test_context_features_do_not_depend_on_same_day_row_order():
    pd.testing.assert_frame_equal(
        canonical(build_features(sample((0, 1)))),
        canonical(build_features(sample((1, 0)))),
    )


def test_pi_ratings_do_not_depend_on_same_day_row_order():
    pd.testing.assert_frame_equal(
        canonical(run_pi(sample((0, 1)))),
        canonical(run_pi(sample((1, 0)))),
    )


def test_venue_and_head_to_head_features_reset_each_season():
    rows = []
    for day in range(1, 6):
        rows.append({
            "date": f"2025-04-{day:02d}", "year": 2025, "league": "TEST",
            "sport": "bs", "home_team": "A", "away_team": "B",
            "home_score": 5, "away_score": 1, "outcome": 1.0,
        })
    rows.append({
        "date": "2026-04-01", "year": 2026, "league": "TEST", "sport": "bs",
        "home_team": "A", "away_team": "B", "home_score": 3, "away_score": 2,
        "outcome": 1.0,
    })
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])

    first_game_2026 = build_features(frame).iloc[-1]

    assert pd.isna(first_game_2026["venue_diff"])
    assert pd.isna(first_game_2026["h2h_diff"])


def _nba_game(date: str, score=(110, 100)) -> dict:
    stamp = pd.Timestamp(date)
    return {
        "date": stamp, "year": stamp.year, "league": "NBA", "sport": "bk",
        "home_team": "A", "away_team": "B", "home_score": score[0],
        "away_score": score[1], "outcome": float(score[0] > score[1]),
    }


def test_cross_year_league_keeps_same_season_across_new_year():
    rows = [_nba_game(f"2025-12-{day:02d}") for day in range(1, 6)]
    rows.append(_nba_game("2026-01-02"))

    january = build_features(pd.DataFrame(rows)).iloc[-1]

    assert january["venue_diff"] == 1.0
    assert january["h2h_diff"] == 1.0


def test_cross_year_league_resets_at_next_season_boundary():
    rows = [_nba_game(f"2026-04-{day:02d}") for day in range(1, 6)]
    rows.append(_nba_game("2026-10-02"))

    october = build_features(pd.DataFrame(rows)).iloc[-1]

    assert pd.isna(october["venue_diff"])
    assert pd.isna(october["h2h_diff"])


def test_jleague_transition_uses_cross_year_key_only_after_2026_change():
    assert season_key("J1리그", pd.Timestamp("2025-03-01")) == 2025
    assert season_key("J1리그", pd.Timestamp("2026-08-07")) == 2026
    assert season_key("J1리그", pd.Timestamp("2027-03-01")) == 2026
    assert season_key("J1백년", pd.Timestamp("2026-03-01")) == 2026


def test_runtime_forms_keep_december_and_january_in_same_nba_season():
    rows = [_nba_game(f"2025-12-{day:02d}") for day in range(1, 4)]
    rows.extend(_nba_game(f"2026-01-{day:02d}") for day in range(1, 3))

    forms, h2h = build_forms(
        pd.DataFrame(rows), season=2026, as_of=pd.Timestamp("2026-01-15"))

    assert forms[("NBA", "A")].w == 5
    assert len(h2h[("NBA", "A", "B")]["games"]) == 5


def test_game_specific_rest_days_do_not_mutate_shared_form():
    shared = Form(team="A", league="KBO", last_date=pd.Timestamp("2026-08-20"))

    today = form_for_game(shared, pd.Timestamp("2026-08-21 18:30"))
    later = form_for_game(shared, pd.Timestamp("2026-08-24 18:30", tz="Asia/Seoul"))

    assert today.rest_days == 1
    assert later.rest_days == 4
    assert shared.rest_days is None
