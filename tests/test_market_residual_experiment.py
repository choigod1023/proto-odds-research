import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from market_residual_experiment import cohort, experiment, predict


def records():
    out = []
    for i in range(24):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        iso = lambda hours: (t + timedelta(hours=hours)).isoformat()
        out.extend([
            dict(record_type="prediction", event_id=str(i), snapshot_id=str(i),
                 as_of=iso(0), captured_at=iso(0), kickoff=iso(2),
                 model=dict(residual_version="v1"),
                 predictions=dict(selection_id="s", market="승패", odds=1.9,
                     score_forecast=dict(contract=dict(sport="bs")),
                     probability_detail=dict(market=.55, ai_candidate=.7 if i%2 else .4))),
            dict(record_type="settlement", event_id=str(i), snapshot_id=str(i),
                 source="official", settlement_version="official-test",
                 settled_at=iso(5), captured_at=iso(6),
                 outcome=dict(selection_id="s", result="hit" if i%2 else "miss"))])
    return out


def run(data):
    return experiment(data, sport="bs", version="v1", minimum=4,
        train_end="2026-01-09T00:00:00Z", calibration_end="2026-01-17T00:00:00Z",
        test_end="2026-01-25T00:00:00Z")


def test_offset_zero_is_market():
    assert predict([dict(market=.6, model=.8)])[0] == pytest.approx(.6)


def test_holdout_outcomes_cannot_change_parameters():
    data = records()
    before = copy.deepcopy(data)
    result = run(data)
    assert data == before
    assert result["split_counts"] == dict(train=8, calibration=8, test=8)
    assert result["promotion_allowed"] is False
    for row in data[32:]:
        if row["record_type"] == "settlement":
            row["outcome"]["result"] = "miss"
    changed = run(data)
    assert result["parameters"] == changed["parameters"]
    assert result["all_test"]["candidate"]["brier"] < result["all_test"]["raw_market"]["brier"]
    assert result["equal_count_selection"]["candidate"]["n"] == result["equal_count_selection"]["market"]["n"] == 2


def test_training_labels_must_be_known_before_boundary():
    data = records()
    data[1]["captured_at"] = "2026-01-10T00:00:00Z"
    assert run(data)["split_counts"]["train"] == 7


@pytest.mark.parametrize("mutation,reason", [
    (lambda p,s: p.update(captured_at=p["kickoff"]), "invalid_or_late_revision"),
    (lambda p,s: s["outcome"].update(selection_id="wrong"), "invalid_or_conflicting_settlement"),
    (lambda p,s: p["predictions"]["probability_detail"].update(ai_candidate=float("nan")), "missing_probability_or_price"),
    (lambda p,s: s.update(event_id="wrong"), "invalid_or_conflicting_settlement"),
])
def test_bad_records_fail_closed(mutation, reason):
    data = records()[:2]
    mutation(*data)
    rows, excluded = cohort(data, sport="bs", version="v1")
    assert not rows and excluded[reason] == 1


def test_latest_unsettled_revision_does_not_fall_back():
    data = records()[:2]
    newer = copy.deepcopy(data[0])
    newer.update(snapshot_id="new", ledger_sequence=2)
    data.append(newer)
    rows, excluded = cohort(data, sport="bs", version="v1")
    assert not rows and excluded["pending"] == 1


def test_conflicting_settlement_excluded_and_empty_not_zero_roi():
    data = records()[:2]
    revised = copy.deepcopy(data[1])
    revised["outcome"]["result"] = "hit"
    data.append(revised)
    assert not cohort(data, sport="bs", version="v1")[0]
    result = run([])
    assert result["status"] == "insufficient_data"
    assert "all_test" not in result


def test_naive_boundaries_rejected():
    with pytest.raises(ValueError):
        experiment([], sport="bs", version="v1", train_end="2026-01-01",
                   calibration_end="2026-02-01", test_end="2026-03-01")


def test_actual_settlement_schema_has_no_event_id():
    data = records()[:2]
    del data[1]["event_id"]
    assert len(cohort(data, sport="bs", version="v1")[0]) == 1
