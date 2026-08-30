from src.internal_factor_validation import validate_internal_factors


def _rows(n=40, candidate=.7, outcome=1):
    return [{"league": "KBO", "observed_at": f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00",
             "market_probability": .55, "internal_probability": candidate,
             "outcome": outcome, "odds": 1.8} for i in range(n)]


def test_small_future_sample_stays_shadow_even_when_metrics_look_better():
    report = validate_internal_factors(_rows(), bootstrap=100)
    kbo = report["leagues"]["KBO"]
    assert kbo["candidate"]["brier"] < kbo["market"]["brier"]
    assert kbo["status"] == "shadow_only"
    assert kbo["cutoff"] is not None


def test_reports_favorite_direction_segments_and_all_leagues():
    report = validate_internal_factors(_rows(candidate=.45), bootstrap=20)
    assert report["leagues"]["KBO"]["segments"]["underdog_flip"] > 0
    assert set(report["leagues"]) == {"MLB", "KBO", "NPB"}
