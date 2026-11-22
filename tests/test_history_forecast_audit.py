from scripts.audit_history_forecasts import audit, normalize
import pytest


def test_same_prices_and_refund_payoffs_are_recomputed_independently():
    rows = [dict(id="a", day="2026-01-01", y=1, odds=[1.8, 2.0], probs={"base": .6, "new": .4}),
            dict(id="b", day="2026-01-02", y=0, odds=[1.8, 2.0], probs={"base": .6, "new": .4}),
            dict(id="c", day="2026-01-03", y=None, odds=[1.8, 2.0], probs={"base": .6, "new": .4})]
    result = audit(rows)["models"]
    assert result["base"]["hits"] == result["new"]["hits"] == 1
    assert result["base"]["decided"] == 2 and result["base"]["refunds"] == 1
    assert result["base"]["conditional_roi"] == pytest.approx(-.2/3)
    assert result["new"]["conditional_roi"] == 0


def test_duplicate_events_and_model_dependent_cohorts_are_rejected():
    row = dict(id="a", day="2026-01-01", y=1, odds=[1.8, 2.0], probs={"base": .6})
    with pytest.raises(ValueError, match="duplicate"):
        audit([row, row])
    with pytest.raises(ValueError, match="inconsistent"):
        audit([row, dict(row, id="b", probs={"new": .6})])


def test_saved_choice_price_must_match_probability_and_source_quote():
    row = dict(year="2026", fixture_id="a", date="2026-01-01", winner="home", prices={"home": 1.8, "away": 2.0},
               arms={"candidate": dict(p_home=.7, side="home", odds=2.0)})
    with pytest.raises(ValueError, match="saved_choice_or_price"):
        normalize(row, "kbo")


def test_future_validation_is_not_counted_as_evaluation():
    assert normalize({"period": "validation"}, "kbl") is None
    assert normalize({"year": "2025"}, "kbo") is None


def test_nba_saved_home_outcome_and_all_comparators_use_shared_quote():
    source = dict(id="nba1", day="2026-01-02", home_win=1, odds=[1.8, 2.1],
                  market_home=.6, candidate=.4, existing_outcome_form=.7)
    result = audit([normalize(source, "nba")])["models"]
    assert result["market_home"]["net_units"] == "0.8"
    assert result["candidate"]["net_units"] == "-1"
    assert normalize(dict(source, day="2025-12-31"), "nba") is None


@pytest.mark.parametrize("kind,date_field", [("kovo", "date"), ("vnl", "day")])
def test_volleyball_saved_outcome_is_home_one_not_raw_price_home_zero(kind, date_field):
    source = dict(id="v1", y=0, odds=[1.4, 3.0], market=.7, candidate=.3)
    source[date_field] = "2026-01-02"
    result = audit([normalize(source, kind)])["models"]
    assert result["candidate"]["net_units"] == "2.0"
    assert result["market"]["net_units"] == "-1"


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -.1, 1.1])
def test_nonfinite_or_out_of_range_forecasts_cannot_enter_report(probability):
    row = dict(id="a", day="2026-01-01", y=1, odds=[1.8, 2.0], probs={"base": probability})
    with pytest.raises(ValueError, match="invalid_probability"):
        audit([row])


def test_daily_drawdown_does_not_impose_unknown_intraday_bet_order():
    rows = [dict(id="a", day="2026-01-01", y=0, odds=[2., 2.], probs={"base": .6}),
            dict(id="b", day="2026-01-01", y=1, odds=[2., 2.], probs={"base": .6}),
            dict(id="c", day="2026-01-02", y=0, odds=[2., 2.], probs={"base": .6})]
    # First day nets zero; only the second day's loss contributes to daily MDD.
    assert audit(rows)["models"]["base"]["daily_mdd_units"] == "1.0"
