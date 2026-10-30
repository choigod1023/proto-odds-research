"""Study F regression checks; no network or production databases."""
import copy
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "experiments/league-market-signal-20261030/study.py"
spec = importlib.util.spec_from_file_location("league_market_signal", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def frame(day, winner=0, refund=False):
    return {"event_id": day, "kickoff": day + "T12:00:00+09:00", "league": "KBL",
        "choices": [{"market_id": "early", "market": "승패", "issue_year": 2026,
            "round": 1, "game_no": "1", "side_index_0based": i, "n_way": 2,
            "odds": price, "hit": i == winner, "refund": refund} for i, price in enumerate([1.5, 5.5])]}


def test_label_flips_do_not_change_predictions_or_selected_parameters():
    data = [frame(f"2026-{month:02}-{day:02}") for month in range(1, 7) for day in range(1, 27)]
    first, _, _, phases = m.make_records(data)
    selected, _ = m.choose(first)
    changed = copy.deepcopy(data)
    for f in changed:
        if phases[m.date_of(f)] == "test":
            for c in f["choices"]:
                c["hit"] = not c["hit"]
    second, _, _, _ = m.make_records(changed)
    assert m.choose(second)[0] == selected
    fields = ("event_id", "market_id", "pick", "market_pick", "p", "phase")
    assert [tuple(r[f] for f in fields) for r in first] == [tuple(r[f] for f in fields) for r in second]


@pytest.mark.parametrize("key", ["hit", "winner", "final_score", "result", "refund", "source_result_label"])
def test_outcome_features_rejected(key):
    with pytest.raises(ValueError):
        m.predict([{"odds": 1.5, "side_index_0based": 0, key: 1}], 1)


@pytest.mark.parametrize("independent,received,expected", [
    (False, "2026-01-01T10:00:00+09:00", False),
    (True, "2026-01-01T12:01:00+09:00", False),
    (True, "2026-01-01T11:45:00+09:00", False),
    (True, "2026-01-01T10:00:00+09:00", True),
])
def test_only_certified_receipt_before_decision_eligible(independent, received, expected):
    q = dict(independent_receipt_verified=independent, received_at=received,
        kickoff="2026-01-01T12:00:00+09:00", decision_at="2026-01-01T11:30:00+09:00")
    assert m.receipt_eligible(q) == expected


def test_product_identity_ignores_result_and_avoids_reissues():
    f = frame("2026-01-01")
    duplicate = copy.deepcopy(f["choices"])
    for c in duplicate:
        c.update(market_id="late", round=2, odds=10.0, hit=not c["hit"])
    f["choices"].extend(duplicate)
    assert m.canonical(f)["ML"][0]["market_id"] == "early"


def test_kbl_margin_market_is_not_moneyline_and_uses_frozen_global_dates():
    f = frame("2026-01-01")
    for c in f["choices"]:
        c["market"] = "승⑤패"
    assert m.canonical(f) == {}
    rows, _, _, phases = m.make_records([f], {"margin5": ("승⑤패",)}, {"2026-01-01": "validation"})
    assert rows and all(r["family"] == "margin5" and r["phase"] == "validation" for r in rows)


def test_bin_boundary_and_abstention():
    qs = [{"odds": p, "side_index_0based": i} for i, p in enumerate((1.49, 1.5, 10.0))]
    assert m.predict(qs, 0) == (0, 0)
    assert m.predict(qs, 1) == (1, 0)
    assert m.predict(qs, 4) == (None, 0)
    assert m.predict(qs, 5) == (2, 0)


@pytest.mark.parametrize("refund", [True, False])
def test_independent_decimal_settlement_matches(refund):
    rows, _, _, _ = m.make_records([frame("2026-01-01", winner=1, refund=refund)])
    check = m.independent_verify(rows)
    assert check["matched"]
    if refund:
        assert m.metrics(rows)["roi"] == 0
        assert m.metrics(rows)["accuracy"] is None
    bad = copy.deepcopy(rows)
    bad[0]["profit_cents"] += 1
    with pytest.raises(AssertionError):
        m.independent_verify(bad)


def test_bh_counts_all_hypotheses_and_is_monotone():
    p = [.001, .03, .2, 1.0]
    assert m.bh(p) == [.004, .06, .26666666666666666, 1.0]
    assert m.bh([.001] + [1.] * 999)[0] == 1


def test_dependence_clusters_fixture_choices_and_sparse_weeks_no_significance():
    rows, _, _, _ = m.make_records([frame("2026-01-01")])
    inf = m.inference(rows * 100, repeats=50, permutations=99)
    assert inf["weeks"] == 1
    assert inf["roi_p"] == inf["accuracy_delta_p"] == 1


def test_power_changes_with_effect_and_disagreement():
    assert m.required_pairs(.03, .5) > m.required_pairs(.03, .2)
    assert m.required_pairs(.03, .2) > m.required_pairs(.05, .2)


def test_unknown_settlement_excluded_not_imputed():
    f = frame("2026-01-01")
    f["choices"][0]["hit"] = None
    rows, _, audit, _ = m.make_records([f])
    assert not rows and audit["incomplete_settlement"] == 1


def test_nonexclusive_labels_rejected():
    f = frame("2026-01-01")
    f["choices"][1]["hit"] = True
    rows, _, audit, _ = m.make_records([f])
    assert not rows and audit["nonexclusive_result_rejected"] == 1


def test_three_way_and_invalid_labels_never_change_quote_only_predictions():
    f = frame("2026-01-01")
    for c in f["choices"]:
        c["n_way"] = 3
        c["market"] = "승무패"
    third = dict(f["choices"][0], side_index_0based=2, odds=3.3, hit=False)
    f["choices"].append(third)
    expected, _ = m.build_predictions([f])
    changed = copy.deepcopy(f)
    for c in changed["choices"]:
        c["hit"] = not c["hit"]  # Invalid two-winner settlement, still same prediction.
    assert m.build_predictions([changed])[0] == expected
    assert m.make_records([changed])[0] == []
    changed["choices"][0]["hit"] = None
    assert m.build_predictions([changed])[0] == expected
    assert m.make_records([changed])[0] == []
    for i, c in enumerate(changed["choices"]):
        c["hit"] = i == 2  # Valid alternative outcome.
    assert m.build_predictions([changed])[0] == expected
