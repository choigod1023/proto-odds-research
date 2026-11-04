from copy import deepcopy
from decimal import Decimal

import pytest

from src.research_vote_rankings import evaluate, grade_selected, select_rules


def fixture(i=0, *, favorite="원정", market="승무패"):
    sides = ["홈", "무", "원정"] if market == "승무패" else ["홈", "원정"]
    probs = ([0.25, 0.20, 0.55] if favorite == "원정" else [0.55, 0.20, 0.25]) if len(sides) == 3 else ([0.4, 0.6] if favorite == "원정" else [0.6, 0.4])
    options = [dict(selection=s, selection_id=f"sel-{s}", offer_id=f"offer-{s}", market=market,
                    label="", line=None, odds=odds, market_probability=p)
               for s, odds, p in zip(sides, ([2.0, 3.2, 4.0] if len(sides) == 3 else [2.0, 4.0]), probs)]
    f = dict(event_id=f"event-{i}", snapshot_id=f"snapshot-{i}", sport="sc", kickoff="2026-11-01T12:00:00Z",
             as_of="2026-11-01T10:00:00Z", captured_at="2026-11-01T10:01:00Z", market_observed_at="2026-11-01T10:00:00Z",
             policy="tipster-vote-v1", selection_source="actual_tipster_vote", market=market, label="", options=options,
             original_selection="홈", original_selection_id="sel-홈", original_offer_id="offer-홈", original_odds=2.0,
             evidence=[dict(id=f"vote-{i}", author=f"author-{i}", side="홈", observed_at="2026-11-01T09:00:00Z", source_url=f"https://example.org/{i}", content_hash=f"hash-{i}")],
             vote_counts={s: int(s == "홈") for s in sides})
    label = dict(event_id=f["event_id"], snapshot_id=f["snapshot_id"], kickoff=f["kickoff"], market=market,
                 label="", n_way=len(sides), result_action="결과보기", result_text="0:1")
    return f, label


def test_alternative_side_uses_its_own_frozen_price():
    f, label = fixture()
    choices = select_rules(f)
    assert choices["frozen_vote"] == "홈"
    assert choices["market_same_product"] == "원정"
    assert choices["market90_vote10"] == "원정"
    assert Decimal(grade_selected(f, label, "원정")["pnl_won"]) == 30000
    assert grade_selected(f, label, "원정")["odds"] == 4.0
    assert Decimal(grade_selected(f, label, "홈")["pnl_won"]) == -10000


def test_outcome_mutation_does_not_change_choices():
    f, label = fixture()
    changed = deepcopy(label)
    changed["result_text"] = "4:0"
    before, after = evaluate([f], [label]), evaluate([f], [changed])
    assert before["choices"] == after["choices"]
    assert before["results"]["frozen_vote"]["hit"] == 0
    assert after["results"]["frozen_vote"]["hit"] == 1


def test_tied_market_has_no_unique_favorite_and_agreement_abstains():
    f, _ = fixture()
    for option, prob in zip(f["options"], [0.4, 0.2, 0.4]):
        option["market_probability"] = prob
    choices = select_rules(f)
    assert choices["market_same_product"] is None
    assert choices["vote_market_agreement"] is None
    assert choices["market90_vote10"] == "홈"


def test_agreement_coverage_and_baseline_are_on_the_same_subset():
    a, la = fixture(0, favorite="홈")
    b, lb = fixture(1, favorite="원정")
    la["result_text"] = "1:0"
    result = evaluate([a, b], [la, lb])
    agree = result["results"]["vote_market_agreement"]
    pair = result["paired"]["vote_market_agreement"]
    assert (agree["bets"], agree["abstained"], agree["coverage_percent"], agree["hit"]) == (1, 1, 50, 1)
    assert pair["common_fixtures"] == 1
    assert pair["baseline_on_same_subset"]["hit"] == 1
    assert pair["baseline_on_same_subset"]["bets"] == 1
    assert pair["changed_pick_count"] == pair["improved"] == pair["worsened"] == 0


def test_exact_paired_counts_with_four_improved_and_one_worsened():
    pairs = [fixture(i) for i in range(5)]
    pairs[-1][1]["result_text"] = "1:0"
    result = evaluate([f for f, _ in pairs], [l for _, l in pairs])
    pair = result["paired"]["market_same_product"]
    assert (pair["common_fixtures"], pair["improved"], pair["worsened"], pair["changed_pick_count"]) == (5, 4, 1, 5)
    assert pair["exploratory_exact_mcnemar_p"] == 0.375


def test_empty_coverage_has_none_accuracy_and_roi():
    f, label = fixture()
    result = evaluate([f], [label])
    agree = result["results"]["vote_market_agreement"]
    assert (agree["bets"], agree["abstained"], agree["coverage_percent"]) == (0, 1, 0)
    assert agree["accuracy_percent"] is None and agree["roi_percent"] is None
    subset = result["paired"]["vote_market_agreement"]["baseline_on_same_subset"]
    assert subset["accuracy_percent"] is None and subset["roi_percent"] is None


@pytest.mark.parametrize("mutation", ["late_vote", "late_capture", "invalid_count", "duplicate_author", "duplicate_id", "side_mismatch"])
def test_bad_vote_evidence_fails(mutation):
    f, _ = fixture()
    if mutation == "late_vote":
        f["evidence"][0]["observed_at"] = "2026-11-01T11:30:00Z"
    elif mutation == "late_capture":
        f["captured_at"] = "2026-11-01T11:30:00Z"
    elif mutation == "invalid_count":
        f["vote_counts"]["홈"] = -1
    elif mutation == "side_mismatch":
        f["evidence"][0]["side"] = "원정"
    else:
        e = deepcopy(f["evidence"][0]); e["author"] = "other"; e["id"] = "other"
        if mutation == "duplicate_author": e["author"] = f["evidence"][0]["author"]
        else: e["id"] = f["evidence"][0]["id"]
        f["evidence"].append(e); f["vote_counts"]["홈"] = 2
    with pytest.raises(ValueError): select_rules(f)


@pytest.mark.parametrize("field", ["source_url", "content_hash"])
@pytest.mark.xfail(strict=True, reason="source URL/content hash duplicates are not currently rejected")
def test_duplicate_source_evidence_fails(field):
    f, _ = fixture()
    e = deepcopy(f["evidence"][0]); e.update(id="another", author="another")
    other = "content_hash" if field == "source_url" else "source_url"
    e[other] = "distinct"
    f["evidence"].append(e); f["vote_counts"]["홈"] = 2
    with pytest.raises(ValueError): select_rules(f)


@pytest.mark.parametrize("field,value", [("event_id", "other"), ("snapshot_id", "other"), ("kickoff", "2026-11-02T12:00:00Z"), ("market", "승패"), ("label", "other"), ("n_way", 2)])
def test_mismatched_result_label_fails(field, value):
    f, label = fixture(); label[field] = value
    with pytest.raises(ValueError): evaluate([f], [label])


def test_duplicate_fixture_and_duplicate_labels_fail():
    f, label = fixture(); other = deepcopy(f); other["snapshot_id"] = "another"
    second_label = deepcopy(label); second_label["snapshot_id"] = "another"
    with pytest.raises(ValueError): evaluate([f, other], [label, second_label])
    with pytest.raises(ValueError): evaluate([f], [label, label])


def test_full_game_three_way_draw_and_two_way_draw_held():
    f, label = fixture(); label["result_text"] = "2:2"
    assert grade_selected(f, label, "무")["hit"] is True
    assert Decimal(grade_selected(f, label, "무")["pnl_won"]) == 22000
    assert grade_selected(f, label, "홈")["hit"] is False
    two, two_label = fixture(1, market="승패"); two_label["result_text"] = "2:2"
    with pytest.raises(ValueError, match="draw"): evaluate([two], [two_label])
