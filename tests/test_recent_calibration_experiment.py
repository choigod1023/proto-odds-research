"""Offline experiment regressions; no server, database or network access."""
import copy
import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from recent_calibration_experiment import (  # noqa: E402
    digest, experiment, fit, holm, main, paired_diagnostic, predict, prepare,
    scores, validate_protocol,
)

FIXTURES = ROOT / "experiments" / "recent-calibration-20260907"


@pytest.fixture
def inputs():
    return tuple(json.loads((FIXTURES / name).read_text(encoding="utf-8"))
                 for name in ("dataset.json", "protocol.json"))


def test_real_snapshot_cohort_and_no_mutation(inputs):
    dataset, protocol = inputs
    original = digest(dataset)
    train, evaluation, excluded = prepare(dataset, protocol)
    assert len(train) == 76 and len(evaluation) == 103
    assert max(r["day"] for r in train) < min(r["day"] for r in evaluation)
    assert all(r["dbVerified"] for r in evaluation)
    assert sum(r["state"] == "hit" for r in evaluation) == 57
    assert excluded == {}
    result = experiment(dataset, protocol)
    assert result["promotion_allowed"] is False
    assert result["pristine_future"] is False
    assert result["baseline"]["n"] == 103
    assert result["evaluation"]["fixed_pick_roi"] == pytest.approx(-0.10844660194174759)
    assert digest(dataset) == original


def test_evaluation_labels_never_change_training_coefficients(inputs):
    dataset, protocol = inputs
    before = experiment(dataset, protocol)
    modified = copy.deepcopy(dataset)
    for row in modified["rows"]:
        if row["day"] >= protocol["evaluation_start"]:
            row["state"] = "miss" if row["state"] == "hit" else "hit"
    after = experiment(modified, protocol)
    for name in before["candidates"]:
        assert before["candidates"][name]["model"] == after["candidates"][name]["model"]
    assert [r["candidates"] for r in before["predictions"]] == [
        r["candidates"] for r in after["predictions"]]


def test_input_order_does_not_change_result(inputs):
    dataset, protocol = inputs
    reordered = copy.deepcopy(dataset)
    reordered["rows"].reverse()
    a, b = experiment(dataset, protocol), experiment(reordered, protocol)
    assert a["candidates"] == b["candidates"]
    assert a["predictions"] == b["predictions"]


def test_identical_duplicate_ignored_and_conflict_rejected(inputs):
    dataset, protocol = inputs
    dataset["rows"].append(copy.deepcopy(dataset["rows"][0]))
    assert prepare(dataset, protocol)[2] == {"identical_duplicate": 1}
    dataset["rows"][-1]["state"] = "hit" if dataset["rows"][-1]["state"] == "miss" else "miss"
    with pytest.raises(ValueError, match="conflicting"):
        prepare(dataset, protocol)


def test_new_revision_same_event_is_not_silently_selected(inputs):
    dataset, protocol = inputs
    other = copy.deepcopy(dataset["rows"][0])
    other["id"] += "_revision"
    dataset["rows"].append(other)
    with pytest.raises(ValueError, match="conflicting"):
        prepare(dataset, protocol)


@pytest.mark.parametrize("state,source", [("void", "official"), ("pending", "official"), ("hit", "score")])
def test_non_official_and_unsettled_excluded(inputs, state, source):
    dataset, protocol = inputs
    row = copy.deepcopy(dataset["rows"][0])
    row.update(state=state, source=source)
    dataset["rows"].append(row)
    assert prepare(dataset, protocol)[2] == {"non_official_or_unsettled": 1}


def test_freeze_and_future_excluded(inputs):
    dataset, protocol = inputs
    dataset["rows"][0]["capturedAt"] = dataset["rows"][0]["kickoff"]
    future = copy.deepcopy(dataset["rows"][-1])
    future.update(id="future", event="future", day="2026-09-07",
                  kickoff="2026-09-07T14:00:00Z", capturedAt="2026-09-07T10:00:00Z")
    dataset["rows"].append(future)
    assert prepare(dataset, protocol)[2] == {"not_before_freeze": 1, "future": 1}


@pytest.mark.parametrize("bad", [0, 1, math.nan, True, "0.6"])
def test_invalid_probability_fails(inputs, bad):
    dataset, protocol = inputs
    dataset["rows"][0]["probability"] = bad
    with pytest.raises(ValueError, match="probability"):
        prepare(dataset, protocol)


def test_kst_day_and_naive_time_rejected(inputs):
    dataset, protocol = inputs
    dataset["rows"][0]["day"] = "2026-09-01"
    with pytest.raises(ValueError, match="KST"):
        prepare(dataset, protocol)
    dataset["rows"][0]["kickoff"] = "2026-09-04T10:00:00"
    with pytest.raises(ValueError, match="timezone"):
        prepare(dataset, protocol)


def test_unseen_league_uses_existing_parents_only(inputs):
    dataset, protocol = inputs
    train, evaluation, _ = prepare(dataset, protocol)
    model = fit(train, protocol["candidates"][2], protocol)
    row = {**evaluation[0], "league": "UNSEEN"}
    parent = {**model, "levels": ["global", "sport"]}
    assert predict(row, model) == predict(row, parent)
    assert 0 < predict(row, model) < 1


def test_two_days_cannot_support_point_zero_five_sign_flip(inputs):
    dataset, protocol = inputs
    _, rows, _ = prepare(dataset, protocol)
    perfect = [.99 if r["state"] == "hit" else .01 for r in rows]
    diagnostic = paired_diagnostic(rows, perfect)
    assert diagnostic["clusters"] == 2
    assert diagnostic["brier_p"] == .5
    assert diagnostic["log_loss_p"] == .5
    assert paired_diagnostic(rows, [r["probability"] for r in rows])["brier_p"] == 1


def test_holm_adjusts_all_eight_comparisons():
    assert holm({str(i): .5 for i in range(8)}) == {str(i): 1 for i in range(8)}
    assert holm({"a": .01, "b": .04, "c": None}) == {"a": .02, "b": .04, "c": None}


def test_group_totals_are_same_evaluation_cohort(inputs):
    result = experiment(*inputs)
    for groups in result["groups"].values():
        assert sum(r["baseline"]["n"] for r in groups.values()) == 103
        for name in result["candidates"]:
            assert sum(r["candidates"][name]["n"] for r in groups.values()) == 103


def test_protocol_cannot_authorize_production(inputs):
    _, protocol = inputs
    protocol["operating_changes_allowed"] = True
    with pytest.raises(ValueError, match="offline-only"):
        validate_protocol(protocol)


def test_cli_creates_new_output_and_preserves_existing_files(inputs, tmp_path):
    dataset, protocol = inputs
    inp, spec, out = (tmp_path / name for name in ("input.json", "protocol.json", "report.json"))
    inp.write_text(json.dumps(dataset), encoding="utf-8")
    spec.write_text(json.dumps(protocol), encoding="utf-8")
    args = ["--input", str(inp), "--protocol", str(spec), "--output", str(out)]
    assert main(args) == 0
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        main(args)
    assert out.read_bytes() == before
    with pytest.raises(SystemExit):
        main(args[:-1] + [str(inp)])
    assert json.loads(inp.read_text()) == dataset


def test_known_brier_and_log_loss():
    value = scores([{"state": "hit"}, {"state": "miss"}], [.8, .2])
    assert value["brier"] == pytest.approx(.04)
    assert value["log_loss"] == pytest.approx(-math.log(.8))


def test_joint_fit_satisfies_penalized_likelihood_gradient(inputs):
    from datetime import date
    from recent_calibration_experiment import key
    dataset, protocol = inputs
    train, _, _ = prepare(dataset, protocol)
    anchor = date.fromisoformat(protocol["evaluation_start"])
    for candidate in protocol["candidates"]:
        model = fit(train, candidate, protocol)
        p = [predict(r, model) for r in train]
        half = candidate["half_life_days"]
        weights = [1.0 if half is None else 2 ** (
            -(anchor-date.fromisoformat(r["day"])).days/half) for r in train]
        weights = [w*len(train)/sum(weights) for w in weights]
        for term in model["coefficients"]:
            term_key = tuple(term["key"])
            gradient = -protocol["ridge"][term_key[0]] * term["value"]
            gradient += sum(w*(int(r["state"] == "hit")-q)
                            for r, q, w in zip(train, p, weights)
                            if key(r, term_key[0]) == term_key)
            assert abs(gradient) < 1e-6
