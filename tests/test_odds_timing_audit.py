import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_timing_audit import (  # noqa: E402
    at_t_minus,
    audit_frames,
    join_observations,
    latest_pregame,
    prepare_archive,
    prepare_snapshots,
)


def archive_row(game_no=1, odds="1.80,1.70"):
    return {
        "year": 2026, "round": 1, "game_no": game_no,
        "date_text": "08.01(토) 19:00", "sport": "bs", "league": "KBO",
        "market_family": "승패", "n_way": 2,
        "home": "홈 5", "away": "3 원정", "odds": odds,
        "result": "홈승", "is_void": False,
    }


def snapshot_row(ts, odds, game_no=1, result="경기전"):
    # result는 결과 라벨이 아니라 관측 당시 시장 상태 필터로만 사용한다.
    return {
        "ts": ts, "year": 2026, "round": 1, "game_no": game_no,
        "sport": "bs", "league": "KBO", "market_family": "승패",
        "n_way": 2, "odds": odds, "result": result,
    }


def test_post_kickoff_prices_never_enter_latest_or_t30():
    archive = prepare_archive(pd.DataFrame([archive_row()]))
    snapshots = prepare_snapshots(pd.DataFrame([
        # 19:00 KST == 10:00 UTC; T-30 target is 09:30 UTC.
        snapshot_row("2026-08-01T09:25:00Z", "1.90,1.60"),
        snapshot_row("2026-08-01T09:35:00Z", "1.85,1.65"),
        snapshot_row("2026-08-01T09:59:00Z", "1.82,1.68"),
        snapshot_row("2026-08-01T10:00:00Z", "1.81,1.69", result="홈패"),
        snapshot_row("2026-08-01T10:05:00Z", "1.80,1.70", result="홈패"),
    ]))
    observations = join_observations(archive, snapshots)

    latest = latest_pregame(observations)
    t30 = at_t_minus(observations)

    assert archive.iloc[0]["kickoff_utc"].isoformat() == "2026-08-01T10:00:00+00:00"
    assert latest.iloc[0]["ts"].isoformat() == "2026-08-01T09:59:00+00:00"
    assert latest.iloc[0]["snapshot_odds"] == (1.82, 1.68)
    assert t30.iloc[0]["ts"].isoformat() == "2026-08-01T09:25:00+00:00"
    assert t30.iloc[0]["snapshot_odds"] == (1.90, 1.60)


def test_post_kick_archive_price_is_reported_but_not_comparable():
    report = audit_frames(
        pd.DataFrame([archive_row()]),
        pd.DataFrame([
            snapshot_row("2026-08-01T10:00:00Z", "1.80,1.70", result="홈패"),
            snapshot_row("2026-08-01T10:05:00Z", "1.80,1.70", result="홈패"),
        ]),
    )

    assert report["latest_pregame"]["comparable_n"] == 0
    assert report["t_minus_30"]["comparable_n"] == 0
    assert report["primary"]["name"] == "t_minus_30"
    assert report["primary"]["target_minutes_before_kickoff"] == 30
    assert report["primary"]["maximum_staleness_minutes"] == 35
    assert report["primary"]["operationally_valid"] is False
    assert report["operationally_valid"] is False
    first_seen = report["archive_price_first_observed"]
    assert first_seen["first_observed_at_or_after_kickoff_n"] == 1
    assert first_seen["examples"][0]["result_from_archive"] == "홈승"


def test_only_explicit_pregame_state_is_eligible_before_kickoff():
    archive = prepare_archive(pd.DataFrame([archive_row()]))
    snapshots = prepare_snapshots(pd.DataFrame([
        # 모두 명목상 킥오프 전이고 배당도 유효하지만 경기전만 허용한다.
        snapshot_row("2026-08-01T09:20:00Z", "1.90,1.60", result="경기전"),
        snapshot_row("2026-08-01T09:21:00Z", "1.89,1.61", result=""),
        snapshot_row("2026-08-01T09:22:00Z", "1.88,1.62", result="-"),
        snapshot_row("2026-08-01T09:23:00Z", "1.87,1.63", result="발매마감"),
        snapshot_row("2026-08-01T09:24:00Z", "1.86,1.64", result="1회초"),
        snapshot_row("2026-08-01T09:25:00Z", "1.85,1.65", result="홈승"),
        # 상태 문자열이 경기전이어도 1.00 잠금 가격은 유효 가격이 아니다.
        snapshot_row("2026-08-01T09:26:00Z", "1.00,1.00", result="경기전"),
    ]))
    observations = join_observations(archive, snapshots)

    latest = latest_pregame(observations)
    t30 = at_t_minus(observations)

    assert latest["ts"].tolist() == [pd.Timestamp("2026-08-01T09:20:00Z")]
    assert t30["ts"].tolist() == [pd.Timestamp("2026-08-01T09:20:00Z")]
    assert latest.iloc[0]["snapshot_result_state"] == "경기전"


def test_missing_snapshot_state_column_fails_closed():
    archive = prepare_archive(pd.DataFrame([archive_row()]))
    snapshots = prepare_snapshots(pd.DataFrame([
        snapshot_row("2026-08-01T09:25:00Z", "1.90,1.60"),
    ]))
    observations = join_observations(archive, snapshots).drop(
        columns="snapshot_result_state"
    )

    assert latest_pregame(observations).empty
    assert at_t_minus(observations).empty
