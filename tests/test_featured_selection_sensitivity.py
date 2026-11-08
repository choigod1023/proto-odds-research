from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import pytest

from scripts.featured_selection_sensitivity import frozen_candidates, grade, metrics, rank, evaluate


def prediction():
    def option(key, market, label, line, selection, odds):
        return dict(selection_id=key, offer_id="o"+key, market=market, label=label,
                    line=line, selection=selection, odds=odds)
    home = option("home", "승패", "", None, "홈", 1.2)
    away = option("away", "승패", "", None, "원정", 4.0)
    under = option("under", "언더오버", "U 200.5", 200.5, "언더", 1.7)
    over = option("over", "언더오버", "U 200.5", 200.5, "오버", 1.8)
    item = dict(under, settlement_rule="total_half", automatic_recommendation=True)
    return dict(record_type="prediction", event_id="event", snapshot_id="snapshot",
        model={"selection_policy": "tipster-vote-v1"}, kickoff="2026-11-06T10:00:00+00:00",
        captured_at="2026-11-06T08:00:00+00:00", as_of="2026-11-06T08:00:00+00:00",
        market_observed_at="2026-11-06T08:00:00+00:00",
        features={"fixture": {"sport": "bk", "league": "NBA"}, "options": [home, away, under, over]},
        predictions=dict(home, action="market_reference", featured_pick=dict(item),
                         market_recommendations={"items": [item]}))


def test_selection_replay_and_removing_price_preference_use_only_frozen_prices():
    p = prediction()
    before = deepcopy(p)
    candidates = frozen_candidates(p)
    assert rank(candidates, True)["selection_id"] == "under"
    assert rank(candidates, False)["selection_id"] == "home"
    # Independent exact rational arithmetic, not the rounded stored probability.
    inverse_sum = Fraction(1, 1)/Fraction("1.2") + Fraction(1, 1)/Fraction("4.0")
    assert candidates[0]["probability"] == pytest.approx(float(1/(Fraction("1.2")*inverse_sum)))
    assert p == before
    p["features"]["options"].pop()
    with pytest.raises(ValueError, match="vector"):
        frozen_candidates(p)


def test_three_way_handicap_has_its_own_probability_vector():
    p = prediction()
    items = []
    for index, (side, odds) in enumerate(zip(("핸디홈", "핸디무", "핸디원정"), (3.0, 3.5, 1.8))):
        item = dict(selection_id=f"h{index}", offer_id=f"oh{index}", market="핸디캡", label="H -1.0",
                    line=-1.0, selection=side, odds=odds)
        p["features"]["options"].append(item)
        items.append(item)
    p["predictions"]["market_recommendations"]["items"].append(
        dict(items[2], settlement_rule="handicap_threeway", automatic_recommendation=True))
    candidates = frozen_candidates(p)
    assert candidates[-1]["probability"] == pytest.approx((1/1.8)/(1/3+1/3.5+1/1.8))


def test_exact_line_and_frozen_price_are_required_for_grade():
    candidate = frozen_candidates(prediction())[1]
    saved = {k: candidate[k] for k in ("selection_id", "offer_id", "market", "line", "settlement_rule", "odds")}
    saved.update(odds="1.70", result="hit", net_units="0.70")
    outcome = {"market_recommendation_results": {candidate["offer_id"]: saved}}
    assert Decimal(grade(candidate, outcome)["net_units"]) == Decimal(".7")
    saved["line"] = 201.5
    with pytest.raises(ValueError, match="product"):
        grade(candidate, outcome)


def test_latest_settlement_common_cohort_and_cutoff_do_not_change_history():
    p = prediction()
    item = p["predictions"]["market_recommendations"]["items"][0]
    line = {k: item[k] for k in ("selection_id", "offer_id", "market", "line", "settlement_rule", "odds")}
    line.update(result="miss", net_units="-1")
    outcome = dict(selection_id="home", result="hit", featured_pick_result=dict(result="miss", net_units="-1"),
                   market_recommendation_results={item["offer_id"]: line})
    latest = dict(record_type="settlement", snapshot_id="snapshot", ledger_sequence=3, outcome=outcome)
    obsolete = dict(latest, ledger_sequence=2, outcome=dict(outcome, result="miss"))
    records = [p, latest, obsolete]
    original = deepcopy(records)
    summary, replay = evaluate(records)
    assert summary["replay_matches"] == 1 and summary["common_settled"] == 1
    assert summary["common"]["price_first"]["roi"] == -1
    assert summary["common"]["probability_first"]["roi"] == .2
    assert summary["common"]["probability_first"]["delta_vs_current"]["low_information"]
    assert records == original
    assert metrics(replay, "primary_only")["average_odds"] == 1.2
    p["captured_at"] = "2026-11-06T09:30:00+00:00"
    assert evaluate(records)[0]["opportunities"] == 0
    with pytest.raises(ValueError, match="Duplicate event"):
        evaluate([prediction(), prediction()])


def test_refund_and_pending_are_not_counted_as_failed_predictions():
    rows = [{"choices": {"r": dict(odds=1.7, market="승패", grade=g)}} for g in
            (dict(result="hit", net_units=".7"), dict(result="void", net_units="0"), None)]
    result = metrics(rows, "r")
    assert (result["settled"], result["decided"], result["hits"], result["pending"]) == (2, 1, 1, 1)
    assert result["roi"] == .35
