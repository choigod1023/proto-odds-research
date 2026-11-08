import math
import json
import gzip

import pytest

from scripts.compare_frozen_multimarket_models import choose, crosscheck, main, metrics, paired_day_interval, profit


def row(**updates):
    return {"date": "2026-05-01", "fixture_id": "one", "round": "260001", "game_no": 3,
            "market": "total", "line": 8.5, "prices": {"under": 1.73, "over": 1.77},
            "outcome": "over", **updates}


def test_exact_product_payout_and_model_choice():
    r = row()
    assert choose(r, {"home_rate": 1., "away_rate": 1.}) == "under"
    assert choose(r, {"home_rate": 9., "away_rate": 9.}) == "over"
    assert profit(r, "over") == .77
    assert profit(r, "under") == -1.
    with pytest.raises(ValueError, match="no exact-product price"):
        profit(r, "home")


def test_half_handicap_is_not_original_draw_refund():
    r = row(market="handicap", line=-1.5, prices={"home": 3., "away": 1.2}, outcome="away")
    assert choose(r, {"home_rate": 1., "away_rate": 1.}) == "away"
    assert profit(r, "away") == .2
    assert profit(r, "home") == -1.


def test_roi_and_drawdown_differ_from_accuracy():
    rows = [row(fixture_id=str(i), picks={"model": "over"},
                returns={"model": p}, outcome=o) for i, (p, o) in enumerate([(-1., "under"), (.77, "over"), (-1., "under")])]
    m = metrics(rows, "model")
    assert m["hits"] == 1 and m["hit_rate"] == 1/3
    assert math.isclose(m["roi"], -1.23/3)
    assert m["max_drawdown_units"] == 1.23


def test_bootstrap_groups_days_and_identical_models_have_zero_difference():
    rows = [row(date=d, returns={"model": v, "market": v})
            for d, v in [("2026-05-01", 1.), ("2026-05-01", -1.), ("2026-05-02", 2.)]]
    m = paired_day_interval(rows, "model", iterations=100)
    assert m["day_clusters"] == 2
    assert m["roi_difference"] == 0
    assert m["descriptive_day_cluster_bootstrap95"] == [0., 0.]


def test_crosscheck_requires_same_price_line_and_score(tmp_path):
    p = tmp_path / "baseline.jsonl"
    original = row(market_selection="over", score=[5, 6])
    other = dict(round=260001, matchSeq=3, market="total", pick=2,
                 line=8.5, score=[5, 6], price=1.77)
    p.write_text(json.dumps(other) + "\n", encoding="utf-8")
    assert crosscheck([original], p)["matched_exact_products"] == 1
    other["price"] = 1.78
    p.write_text(json.dumps(other) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline mismatch"):
        crosscheck([original], p)


def test_missing_forecast_is_audited_in_common_cohort(tmp_path, monkeypatch):
    cohort = tmp_path / "cohort.json"
    forecasts = tmp_path / "forecasts.json.gz"
    output = tmp_path / "output.json"
    cohort.write_text(json.dumps([row()]), encoding="utf-8")
    forecasts.write_bytes(gzip.compress(b'{}'))
    monkeypatch.setattr("sys.argv", ["compare", "--cohort", str(cohort),
                                    "--forecasts", str(forecasts), "--output", str(output)])
    main()
    result = json.loads(output.read_bytes())
    assert len(result["excluded_common_cohort"]) == 1
    assert "missing_common_forecast" in result["excluded_common_cohort"][0]["reason"]
    assert result["markets"]["total"]["models"]["m0_actual"]["all"]["n"] == 0


def test_daily_drawdown_is_invariant_to_intraday_fixture_order():
    rows = [row(date="2026-05-01", fixture_id=str(i), picks={"model": "over"},
                returns={"model": v}) for i, v in enumerate([-1., 2., -1.])]
    assert metrics(rows, "model")["daily_close_max_drawdown_units"] == 0
    for r in rows:
        r["fixture_id"] = str(3-int(r["fixture_id"]))
    assert metrics(rows, "model")["daily_close_max_drawdown_units"] == 0
