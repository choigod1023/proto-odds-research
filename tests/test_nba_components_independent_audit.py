from copy import deepcopy
import math
import pytest
from scripts.audit_nba_components_comparison import audit


def inputs():
    old = [dict(id="a", espn_id="100", day="2025-10-01", home_win=1, teams=["BOS", "LAL"], scores=[100, 90],
                history_event_ids=[["old-home"], ["old-away"]], odds=[1.8, 2.0],
                market_home=10/19, candidate=.6, existing_outcome_form=.4)]
    row = {k: deepcopy(v) for k, v in old[0].items() if k not in ("market_home", "candidate", "existing_outcome_form")}
    row.update(period="A", probabilities=dict(market=10/19, prior_efficiency=.6, prior_outcome_form=.4, components=.7))
    prices = [dict(id="a", day="2025-10-01", league="NBA", odds=[1.8, 2.0], y=0)]
    return [row], old, prices


def test_original_label_direction_payoffs_and_natural_logloss():
    result = audit(*inputs())["groups"]["A"]["models"]
    assert result["components"]["roi"] == pytest.approx(.8)
    assert result["prior_outcome_form"]["roi"] == -1
    assert result["components"]["logloss"] == pytest.approx(-math.log(.7))


@pytest.mark.parametrize("field,value", [("odds", [1.9, 2.]), ("espn_id", "different"), ("history_event_ids", [["future"], []])])
def test_changed_quote_or_history_cannot_be_silently_compared(field, value):
    rows, old, prices = inputs()
    rows[0][field] = value
    with pytest.raises(ValueError, match="frozen_identity_price_or_history_changed"):
        audit(rows, old, prices)


def test_newly_fitted_old_model_is_not_a_frozen_comparator():
    rows, old, prices = inputs()
    rows[0]["probabilities"]["prior_efficiency"] = .9
    with pytest.raises(ValueError, match="frozen_baseline_probability_changed"):
        audit(rows, old, prices)


def test_omitted_events_require_explicit_subset_and_are_disclosed():
    rows, old, prices = inputs()
    old.append(dict(old[0], id="b"))
    with pytest.raises(ValueError, match="incomplete_common_cohort"):
        audit(rows, old, prices)
    result = audit(rows, old, prices, allow_subset=True)
    assert result["excluded_ids"] == ["b"] and result["coverage"] == .5


def test_nonfinite_probability_and_period_mislabel_are_rejected():
    rows, old, prices = inputs()
    rows[0]["probabilities"]["components"] = float("nan")
    with pytest.raises(ValueError, match="invalid_probability"):
        audit(rows, old, prices)
    rows[0]["probabilities"]["components"] = .7
    rows[0]["period"] = "B"
    with pytest.raises(ValueError, match="wrong_evaluation_period"):
        audit(rows, old, prices)


def test_duplicate_events_are_not_double_counted():
    rows, old, prices = inputs()
    with pytest.raises(ValueError, match="duplicate_prediction_id"):
        audit(rows+rows, old, prices)


def test_source_period_alias_is_only_a_metadata_translation():
    rows, old, prices = inputs()
    expected = audit(rows, old, prices)
    rows[0]["period"] = "validation"
    assert audit(rows, old, prices) == expected


def test_changed_pick_description_preserves_both_outcomes_and_quotes():
    rows, old, prices = inputs()
    changed = audit(rows, old, prices)["groups"]["A"]["components_switches"]["prior_outcome_form"]
    assert changed["n"] == 1
    assert changed["components"]["hits"] == 1 and changed["reference"]["hits"] == 0
    assert changed["components"]["mean_odds"] == 1.8 and changed["reference"]["mean_odds"] == 2.
