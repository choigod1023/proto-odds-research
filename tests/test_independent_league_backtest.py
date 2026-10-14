import copy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from independent_league_backtest import (  # noqa: E402
    diagnostic, experiment, fit, holm, main, metrics_per_row, normalize,
    predict, run_league, validate_protocol,
)


@pytest.fixture
def protocol():
    p = json.loads((ROOT / "experiments/independent-leagues-20260907/protocol.json").read_text())
    p.update(minimum_training_markets=8, minimum_evaluation_markets=8,
             minimum_evaluation_events=8, minimum_evaluation_weeks=2,
             evaluation_end="2025-06-30", bootstrap_repeats=100)
    return p


def archive_row(**kwargs):
    return {"year": 2024, "round": 40, "game_no": 1, "date_text": "09.01(일) 19:00",
            "sport": "bs", "league": "A", "market_family": "승패", "booking_class": "2-way",
            "market_label": "", "n_way": 2, "home": "TeamA 2", "away": "1 TeamB",
            "odds": "1.6,2.1", "result": "홈승", "is_void": False, **kwargs}


def synthetic(league="A"):
    rows = []
    for start, count in [("2023-03-01", 30), ("2024-07-01", 30),
                         ("2025-01-01", 30), ("2025-04-01", 30)]:
        for i, day in enumerate(pd.date_range(start, periods=count, freq="2D")):
            q = [.42+.01*(i % 15), .58-.01*(i % 15)]
            token = league+"-"+day.date().isoformat()
            rows.append({"sport": "bs", "league": league, "market": "승패", "n_way": 2,
                         "day": day.date().isoformat(), "kickoff": day.isoformat(),
                         "event": token, "offer": token, "odds": [1.7, 1.9],
                         "q": q, "winner": int(i % 3 == 0)})
    return rows


def test_normalization_deduplicates_and_rejects_conflicting_prices(protocol):
    rows = [archive_row(), archive_row(round=41)]
    clean, q = normalize(pd.DataFrame(rows), protocol)
    assert len(clean) == 1
    assert q["excluded_or_duplicates"]["duplicate_same_offer"] == 1
    rows.append(archive_row(odds="1.7,2.0"))
    clean, q = normalize(pd.DataFrame(rows), protocol)
    assert clean == []
    assert q["excluded_or_duplicates"]["conflicting_offers_excluded"] == 1


def test_conflicting_winners_all_excluded(protocol):
    clean, q = normalize(pd.DataFrame([archive_row(), archive_row(result="홈패")]), protocol)
    assert not clean
    assert q["excluded_or_duplicates"]["conflicting_offers_excluded"] == 1


def test_same_day_doubleheader_and_new_year(protocol):
    rows = [archive_row(date_text="09.01(일) 14:00"), archive_row(date_text="09.01(일) 19:00"),
            archive_row(year=2025, round=1, date_text="12.31(화) 19:00")]
    clean, _ = normalize(pd.DataFrame(rows), protocol)
    assert len(clean) == 3
    assert len({r["event"] for r in clean}) == 3
    assert clean[-1]["day"] == "2024-12-31"


@pytest.mark.parametrize("changes", [
    {"is_void": True}, {"is_void": "unknown"}, {"result": "경기전"},
    {"result": "오버"}, {"odds": "nan,2.0"}, {"odds": "1,2.0"},
    {"market_family": "전반승패"}, {"date_text": "invalid"},
])
def test_invalid_or_unsupported_archive_rows_excluded(protocol, changes):
    assert normalize(pd.DataFrame([archive_row(**changes)]), protocol)[0] == []


def test_full_result_space_preserves_soccer_draw_and_handicap_draw(protocol):
    rows = [archive_row(sport="sc", market_family="승무패", n_way=3,
                        odds="2.1,3.1,3.2", result="무승부"),
            archive_row(sport="sc", market_family="핸디캡", market_label="H +1.0", n_way=3,
                        odds="2.1,3.1,3.2", result="핸디무")]
    clean, _ = normalize(pd.DataFrame(rows), protocol)
    assert len(clean) == 2 and all(r["winner"] == 1 for r in clean)


def test_no_cross_league_training_or_prediction(protocol):
    train = synthetic("A")[:30]
    candidate = protocol["candidates"][0]
    model = fit(train, "2025-01-01", candidate, protocol["ridge"])
    with pytest.raises(ValueError, match="cross-league"):
        fit(train + synthetic("B")[:30], "2025-01-01", candidate, protocol["ridge"])
    with pytest.raises(ValueError, match="cannot cross"):
        predict(synthetic("B")[:2], model)


def test_other_league_outcomes_do_not_change_a_model(protocol):
    a, b = synthetic("A"), synthetic("B")
    first = experiment(a+b, {}, protocol)["leagues"]["bs|A"]
    for row in b:
        row["winner"] = 1-row["winner"]
    second = experiment(a+b, {}, protocol)["leagues"]["bs|A"]
    # Multiple-testing correction depends on the testing family, not coefficients.
    assert first["folds"] == second["folds"]
    for name in first["candidates"]:
        assert first["candidates"][name]["metrics"] == second["candidates"][name]["metrics"]
        assert first["candidates"][name]["diagnostic"] == second["candidates"][name]["diagnostic"]


def test_same_fold_future_outcomes_cannot_change_model(protocol):
    rows = synthetic()
    _, folds, _ = run_league(rows, protocol)
    changed = copy.deepcopy(rows)
    for row in changed:
        if row["day"] >= "2025-01-01":
            row["winner"] = 1-row["winner"]
    _, changed_folds, _ = run_league(changed, protocol)
    assert folds[0] == changed_folds[0]
    # Predetermined rolling retraining MAY consume completed earlier-quarter labels.
    assert folds[1]["models"] != changed_folds[1]["models"]
    assert folds[0]["train_cutoff_exclusive"] == "2024-12-25"
    assert folds[1]["train_cutoff_exclusive"] == "2025-03-25"
    assert all(m["train_last"] < f["train_cutoff_exclusive"]
               for f in folds for m in f["models"].values())


def test_embargo_and_low_sample_no_borrowing(protocol):
    rows = synthetic()
    rows.extend({**rows[0], "day": "2024-12-26", "kickoff": "2024-12-26T00:00:00",
                 "offer": "embargo", "event": "embargo"} for _ in range(1))
    evaluated, folds, _ = run_league(rows, protocol)
    assert folds[0]["models"]["independent"]["train_n"] == 60
    assert len(evaluated) == 60
    short = [r for r in synthetic("B") if r["day"] >= "2025-01-01"]
    protocol["minimum_training_markets"] = 100
    assert run_league(short, protocol)[0] == []


def test_order_invariance_and_input_immutability(protocol):
    rows = synthetic()
    original = copy.deepcopy(rows)
    a = run_league(rows, protocol)
    b = run_league(list(reversed(rows)), protocol)
    assert a == b
    assert rows == original


def test_multiclass_fit_probabilities_and_correct_metrics(protocol):
    rows = [{**r, "sport": "sc", "market": "승무패", "n_way": 3,
             "q": [.4,.3,.3], "odds": [2.,3.,3.], "winner": i % 3}
            for i, r in enumerate(synthetic()[:30])]
    model = fit(rows, "2025-01-01", protocol["candidates"][0], 10.)
    p = predict(rows, model)
    assert np.allclose(p.sum(axis=1), 1)
    assert np.all((p > 0) & (p < 1))
    m = metrics_per_row({"winner": 1, "odds": [2,3,4]}, [.2,.7,.1])
    assert m["brier"] == pytest.approx(.14)
    assert m["hit"] == 1 and m["profit"] == 2


def test_week_blocks_deterministic_and_zero_difference(protocol):
    rows, _, _ = run_league(synthetic(), protocol)
    for row in rows:
        row["metrics"]["same"] = row["metrics"]["baseline"]
    a = diagnostic(rows, "same", protocol, 4)
    assert a == diagnostic(rows, "same", protocol, 4)
    assert a["gain"] == [0.,0.]
    assert a["p_two_sided"] == [1.,1.]
    assert a["calendar_weeks"] >= a["active_weeks"]
    assert a["ci95"] == [[0.,0.],[0.,0.]]


def test_holm_and_offline_output(protocol):
    assert holm({"a": .01, "b": .04}) == {"a": .02, "b": .04}
    result = experiment(synthetic(), {}, protocol)
    assert result["production_allowed"] is False and result["pristine_future"] is False
    protocol["production_allowed"] = True
    with pytest.raises(ValueError, match="offline"):
        validate_protocol(protocol)


def test_output_cannot_overwrite_input(tmp_path):
    existing = tmp_path / "archive.csv"
    existing.write_text("keep")
    with pytest.raises(SystemExit):
        main(["--input",str(existing),"--protocol",str(existing),"--output",str(existing)])
    assert existing.read_text() == "keep"
