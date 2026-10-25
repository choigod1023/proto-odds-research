"""Synthetic fixtures only: no model selection, held-out data, or live services."""

from collections import Counter
from decimal import Decimal
import json

import numpy as np
import pandas as pd
import pytest

from scripts.sport_tournament_paper import RULES, _price, paper


def games(days=("2024-01-01",), *, teams=3, y=0, odds=(2.0, 3.0, 4.0)):
    return pd.DataFrame([
        dict(date=str(day)[:10], kickoff="18:00", league="L", home_team=f"Home {i}",
             away_team=f"Away {i}", y=y, odds=list(odds))
        for day in days for i in range(teams)
    ])


def evaluate(data, *, p=None, weekly=3, rule="probability"):
    if p is None:
        p = np.tile([.6, .25, .15], (len(data), 1))
    return paper(data, p, weekly, rule)


def selection(result):
    return [(t["date"], t["selected_ids"]) for t in result["tickets_detail"]]


@pytest.mark.parametrize("rule", RULES)
def test_results_do_not_select_tickets(rule):
    data = games(["2024-01-01", "2024-01-02", "2024-01-03"])
    before = evaluate(data, rule=rule)
    data["y"] = 2
    after = evaluate(data, rule=rule)
    assert selection(before) == selection(after)
    assert before["net"] == 90_000
    assert after["net"] == -30_000


def test_rank_order_top_two_distinct_and_ties_ignore_input_order():
    data = games(teams=4)
    p = np.array([[.5, .5, 0], [.5, .5, 0], [.6, .2, .2], [.7, .2, .1]])
    data["odds"] = [[2.2, 2.2, 3.0]] * 4
    result = evaluate(data, p=p, rule="wide_edge")
    ticket = result["tickets_detail"][0]
    assert [leg["home_team"] for leg in ticket["legs"]] == ["Home 3", "Home 2"]
    assert len(set(ticket["event_ids"])) == 2
    order = [3, 1, 0, 2]
    assert result == evaluate(data.iloc[order], p=p[order], rule="wide_edge")
    ties = evaluate(data.iloc[:2], p=p[:2], rule="wide_edge")
    assert [leg["outcome"] for leg in ties["tickets_detail"][0]["legs"]] == [0, 0]
    assert ties == evaluate(data.iloc[:2].iloc[::-1], p=p[:2][::-1], rule="wide_edge")


def test_one_game_with_multiple_eligible_outcomes_is_not_a_ticket():
    data = games(teams=1, odds=(2.2, 2.2, 3.0))
    assert evaluate(data, p=np.array([[.5, .5, 0]]), rule="wide_edge")["tickets"] == 0


@pytest.mark.parametrize("rule,prob,odds,expected", [
    ("probability", .55, 1.5, 1),
    ("probability", .549, 2.0, 0),
    ("probability", .7, 1.499, 0),
    ("positive_edge", .55, 2.0, 1),
    ("positive_edge", .625, 1.6, 0),
    ("positive_edge", .625, 1.601, 1),
    ("wide_edge", .5, 2.06, 1),
    ("wide_edge", .5, 2.059, 0),
    ("wide_edge", .499, 3.0, 0),
    ("high_confidence", .6, 2.0, 1),
    ("high_confidence", .599, 2.0, 0),
    ("high_confidence", 1.0, 1.0, 0),
    ("high_confidence", .8, 1.4, 1),
])
def test_fixed_rule_boundaries(rule, prob, odds, expected):
    data = games(teams=2)
    data["odds"] = odds
    data["y"] = 1
    result = evaluate(data, p=np.full(2, prob), rule=rule)
    assert result["tickets"] == expected


def test_edge_ranking_is_different_from_probability_ranking():
    data = games(teams=3)
    data["odds"] = [[1.6, 4, 5], [2, 4, 5], [3, 4, 5]]
    p = np.array([[.8, .1, .1], [.7, .15, .15], [.6, .2, .2]])
    prob = evaluate(data, p=p)
    edge = evaluate(data, p=p, rule="positive_edge")
    assert [x["home_team"] for x in prob["tickets_detail"][0]["legs"]] == ["Home 0", "Home 1"]
    assert [x["home_team"] for x in edge["tickets_detail"][0]["legs"]] == ["Home 2", "Home 1"]


def test_calendar_month_cap_includes_pending_and_resets():
    data = games(pd.date_range("2024-01-01", "2024-02-29"), y=1)
    result = evaluate(data)
    assert result["tickets"] == 10
    assert result["monthly_net"] == {"2024-01": -50_000, "2024-02": -50_000}
    assert result["max_drawdown"] == 100_000
    assert result["worst_month"] == -50_000
    running = Counter()
    for ticket in result["tickets_detail"]:
        # A newly pending ticket must fit the net budget BEFORE its result.
        assert running[ticket["month"]] - ticket["stake"] >= -50_000
        running[ticket["month"]] += ticket["profit"]


def test_budget_is_net_loss_not_gross_loss_or_turnover():
    data = games(pd.date_range("2024-01-01", "2024-01-31"), y=1)
    data.loc[data["date"] == "2024-01-01", "y"] = 0
    result = evaluate(data)
    assert result["tickets"] == 9  # one +30k win finances eight -10k losses
    assert result["net"] == -50_000
    assert result["stake"] == 90_000
    assert result["max_drawdown"] == 80_000


def test_later_budget_may_change_selection_but_not_earlier_days():
    data = games(pd.date_range("2024-01-01", "2024-01-31"), y=1)
    losing = evaluate(data)
    data.loc[data["date"] == "2024-01-01", "y"] = 0
    winning = evaluate(data)
    assert selection(winning)[:5] == selection(losing)
    assert winning["tickets"] > losing["tickets"]


def test_month_boundary_settlement_belongs_to_ticket_month():
    data = games(["2024-01-31", "2024-02-01"], y=1)
    result = evaluate(data)
    assert result["monthly_net"] == {"2024-01": -10_000, "2024-02": -10_000}
    assert result["tickets_detail"][0]["settlement_date"] == "2024-02-01"
    assert result["tickets_detail"][1]["settlement_date"] == "2024-02-02"


@pytest.mark.parametrize("weekly", [2, 3])
def test_iso_week_cap_and_at_most_one_ticket_per_day(weekly):
    data = games(pd.date_range("2024-12-23", "2025-01-12"), teams=5)
    result = evaluate(data, weekly=weekly)
    week_counts = Counter(tuple(pd.Timestamp(t["date"]).isocalendar()[:2])
                          for t in result["tickets_detail"])
    assert list(week_counts.values()) == [weekly] * 3
    assert result["tickets"] == 3 * weekly
    dates = [t["date"] for t in result["tickets_detail"]]
    assert len(dates) == len(set(dates))


@pytest.mark.parametrize("a,b,price", [
    (1.501, 2.0, "3.0"), (1.555, 1.555, "2.5"),
    (1.6, 1.75, "2.8"), (1.50001, 2.0, "3.0"), (1.505, 2, "3.1"),
])
def test_decimal_product_truncate_then_ceiling(a, b, price):
    assert _price(a, b) == Decimal(price)


def test_rounded_price_drives_cash_and_metrics():
    data = games(teams=2, odds=(1.555, 3, 4))
    result = evaluate(data)
    assert result["tickets_detail"][0]["price"] == 2.5
    assert (result["wins"], result["net"], result["roi"], result["stake"]) == (1, 15_000, 1.5, 10_000)
    assert result["profitable_months"] == 1
    assert result["max_drawdown"] == 0


def test_empty_calendar_months_are_in_denominator():
    data = games(["2024-01-01", "2024-04-01"])
    result = evaluate(data)
    assert result["monthly_net"] == {"2024-01": 30_000, "2024-02": 0, "2024-03": 0, "2024-04": 30_000}
    assert result["months"] == 4
    assert result["profitable_months"] == 2
    assert result["profitable_month_rate"] == .5
    assert result["worst_month"] == 0


def test_ineligible_days_still_define_period():
    data = games(["2024-01-01", "2024-04-01"])
    result = evaluate(data, p=np.tile([.4, .3, .3], (len(data), 1)))
    assert result["months"] == 4
    assert result["roi"] is None
    assert result["net"] == result["stake"] == result["tickets"] == 0
    assert result["profitable_month_rate"] == 0


def test_empty_input_and_json_safety():
    result = evaluate(games().iloc[:0])
    assert result["months"] == 0
    assert result["tickets_detail"] == []
    assert result["monthly_net"] == {}
    assert result["roi"] is result["profitable_month_rate"] is None
    json.dumps(result, allow_nan=False)
    result = evaluate(games())
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert len(result["warnings"]) == 4


def test_duplicate_events_fail_instead_of_double_betting():
    data = games()
    duplicate = data.iloc[[0]].copy()
    duplicate["odds"] = [[3.0, 4.0, 5.0]]
    duplicate["kickoff"] = "2024-01-01 18:00:00"
    with pytest.raises(ValueError, match="duplicate event"):
        evaluate(pd.concat([data, duplicate], ignore_index=True))


def test_doubleheader_distinct_kickoff_is_distinct_game():
    data = games(teams=1)
    later = data.copy()
    later["kickoff"] = "21:00"
    result = evaluate(pd.concat([data, later], ignore_index=True))
    assert result["tickets"] == 1
    assert len(set(result["tickets_detail"][0]["event_ids"])) == 2


@pytest.mark.parametrize("label", [None, np.nan, pd.NA, -1, 3, .5])
def test_bad_labels_fail_even_when_ineligible(label):
    data = games()
    data["y"] = label
    with pytest.raises(ValueError, match="Invalid row"):
        evaluate(data, p=np.tile([.4, .3, .3], (len(data), 1)))


@pytest.mark.parametrize("weekly", [1, 4, 2.0, True, "3"])
def test_invalid_weekly(weekly):
    with pytest.raises(ValueError, match="weekly"):
        evaluate(games(), weekly=weekly)


@pytest.mark.parametrize("p", [np.ones((2, 3)), np.full((3, 3), np.nan),
                              np.array([[1.1, -.1, 0]] * 3), np.ones((3, 3)),
                              np.ones((3, 0)), np.ones((3, 1, 1))])
def test_invalid_probabilities(p):
    with pytest.raises(ValueError):
        evaluate(games(), p=p)


@pytest.mark.parametrize("column,value", [
    ("date", None), ("kickoff", None), ("kickoff", "2024-01-02 18:00"),
    ("league", " "), ("home_team", None), ("away_team", ""),
    ("odds", [2, 3]), ("odds", [2, np.nan, 4]), ("odds", [.9, 3, 4]),
])
def test_invalid_game_fields(column, value):
    data = games()
    data[column] = pd.Series([value] * len(data), dtype=object)
    with pytest.raises(ValueError, match="Invalid row"):
        evaluate(data)


def test_bad_rule_and_missing_column():
    with pytest.raises(ValueError, match="rule"):
        evaluate(games(), rule="invented")
    with pytest.raises(ValueError, match="DataFrame"):
        evaluate(games().drop(columns="y"))


def test_inputs_unchanged_and_duplicate_index_supported():
    data = games()
    data.index = [0, 0, 0]
    snapshot = data.copy(deep=True)
    p = np.tile([.6, .25, .15], (len(data), 1))
    copy_p = p.copy()
    evaluate(data, p=p)
    pd.testing.assert_frame_equal(data, snapshot)
    np.testing.assert_array_equal(p, copy_p)


def test_audit_contains_readable_event_and_outcome_identity():
    ticket = evaluate(games())["tickets_detail"][0]
    for leg in ticket["legs"]:
        assert leg["event_identity"] == [leg["date"], leg["kickoff"], leg["league"],
                                          leg["home_team"], leg["away_team"]]
        assert leg["id"] in ticket["selected_ids"]
        assert leg["event_id"] in ticket["event_ids"]
        assert leg["outcome"] == 0
