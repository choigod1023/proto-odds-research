from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from context_features import build_lineup_workload, build_schedule_context  # noqa: E402
from free_context import (  # noqa: E402
    Observation,
    assess_crowding,
    centered_log_ratio,
    empirical_percentile,
    exp_workload,
    haversine_km,
)
from weather_watch import HOURLY, _FakeSession, fetch_forecast, load_venues  # noqa: E402


def test_observation_rejects_naive_time_and_preserves_three_times():
    now = datetime.now(timezone.utc)
    row = Observation("test", "game-1", "temperature", 22.0, now, now, now, "C")
    assert row.to_dict()["valid_at"].endswith("+00:00")
    with pytest.raises(ValueError):
        Observation("test", "game-1", "temperature", 22.0,
                    datetime.now(), now, now)


def test_compositional_crowding_labels_unexplained_extreme():
    q = [0.72, 0.17, 0.11]
    p = [0.55, 0.25, 0.20]
    a = assess_crowding(q, p, [0.60, 0.23, 0.17],
                        market_percentile=0.97, uncertainty_clr=0.15)
    assert a.label == "설명되지 않는 쏠림"
    assert a.local_skew_clr > 0
    assert a.unexplained_z > 1.64
    assert centered_log_ratio(q, 0) > centered_log_ratio(p, 0)
    assert empirical_percentile([0.1, 0.2, 0.3], 0.25) == pytest.approx(2 / 3)


def test_supported_favorite_can_be_already_priced():
    a = assess_crowding([0.66, 0.20, 0.14], [0.64, 0.21, 0.15],
                        market_percentile=0.85, incremental_gain=-0.001)
    assert a.label == "이미 가격 반영"


def test_workload_never_uses_future_events():
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    events = [(now - timedelta(days=3), 90), (now + timedelta(days=1), 999)]
    assert exp_workload(events, now, tau_days=7) < 90


def test_schedule_context_uses_previous_venue_and_strictly_previous_games():
    matches = pd.DataFrame([
        {"date": "2026-08-01", "league": "K리그1", "home_team": "FC서울", "away_team": "전북현대"},
        {"date": "2026-08-04", "league": "K리그1", "home_team": "포항스틸", "away_team": "FC서울"},
    ])
    out = build_schedule_context(matches)
    assert out.loc[0, "games_7d_home"] == 0
    assert out.loc[1, "games_7d_away"] == 1
    assert out.loc[1, "rest_away"] == 3
    assert out.loc[1, "travel_away_km"] > 200
    assert 250 < haversine_km(37.56, 126.97, 36.02, 129.34) < 350


def test_schedule_context_resets_season_break():
    matches = pd.DataFrame([
        {"date": "2025-11-01", "league": "K리그1", "home_team": "FC서울", "away_team": "전북현대"},
        {"date": "2026-03-01", "league": "K리그1", "home_team": "포항스틸", "away_team": "FC서울"},
    ])
    out = build_schedule_context(matches)
    assert pd.isna(out.loc[1, "rest_away"])
    assert out.loc[1, "road_streak_away"] == 0


def test_lineup_workload_is_pre_match_state():
    xi = tuple(str(i) for i in range(11))
    lineups = pd.DataFrame([
        {"date": "2026-08-01", "team": "서울", "opp": "전북", "is_home": True, "xi": xi},
        {"date": "2026-08-04", "team": "서울", "opp": "포항", "is_home": False, "xi": xi},
    ])
    out = build_lineup_workload(lineups)
    assert out.loc[0, "xi_workload_mean"] == 0
    assert 50 < out.loc[1, "xi_workload_mean"] < 70


def test_weather_snapshot_keeps_observed_and_valid_times_separate():
    venue = load_venues("K리그1")[0]
    snap = fetch_forecast(venue, session=_FakeSession())
    assert snap["observed_at"] <= snap["fetched_at"]
    assert snap["valid_from"] == snap["hourly"]["valid_at"][0]
    assert set(HOURLY).issubset(snap["hourly"])
