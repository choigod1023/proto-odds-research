from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import score_selection_replay as replay
from team_style_archive import normalize


def protocol():
    return json.loads((replay.ROOT/"experiments/score-selection-20260907/protocol.json").read_text(encoding="utf-8"))


def rows(n=365):
    return [dict(day=(pd.Timestamp("2024-01-01")+pd.Timedelta(days=i)).date().isoformat(),
                 kickoff=(pd.Timestamp("2024-01-01 14:00")+pd.Timedelta(days=i)).isoformat(),
                 event=str(i), offer=str(i), sport="bs", league="NPB", market="승패", n_way=2,
                 market_label="", home_team="A", away_team="B", q=[.6, .4], odds=[1.55, 2.1],
                 score_p=[.5+i%3*.07, .5-i%3*.07], score_available=True,
                 winner=int(i%3 == 0), is_void=False) for i in range(n)]


def pick(i=0, hit=1, odds=1.6, **change):
    return dict(id=str(i), event=str(i), day=f"2025-01-{i%28+1:02d}", sport="bs", league="NPB",
                market="승패", n_way=2, odds=odds, q=.6, hit=hit,
                profit=odds-1 if hit else -1., void=False, score_available=True) | change


def test_protocol_valid_and_temporal_guard():
    p = protocol(); replay.validate_protocol(p)
    p["selection_cutoff_exclusive"] = "2024-12-31"
    with pytest.raises(ValueError, match="embargo"):
        replay.validate_protocol(p)


@pytest.mark.parametrize("field,value", [("production_allowed", True), ("selector_ridge", 0),
    ("half_life_days", -1), ("sports", ["vl"]), ("blend_grid", [1.1])])
def test_bad_protocol(field, value):
    p = protocol(); p[field] = value
    with pytest.raises(ValueError): replay.validate_protocol(p)


def test_selector_and_blend_never_read_outer_targets_or_features():
    p, source = protocol(), rows(700)
    frozen = deepcopy(source)
    for r in frozen:
        if r["day"] >= p["selection_cutoff_exclusive"]:
            r.update(winner=1-r["winner"], score_p=[.001, .999], q=[.1, .9])
    for score in (False, True):
        assert replay.fit_selector(source, p, score) == replay.fit_selector(frozen, p, score)
    assert replay.choose_blend(source, p) == replay.choose_blend(frozen, p)


def test_selector_sparse_and_cross_scope():
    p = protocol()
    assert replay.fit_selector(rows(10), p, True)["status"] == "insufficient_inner_history"
    mixed = rows(); mixed[0]["league"] = "MLB"
    with pytest.raises(ValueError, match="pool"):
        replay.fit_selector(mixed, p, True)
    with pytest.raises(ValueError, match="pool"):
        replay.choose_blend(mixed, p)


def test_selector_output_finite_and_fallback_identity():
    p, source = protocol(), rows()
    model = replay.fit_selector(source, p, True)
    assert model["status"] == "trained_pre2025"
    assert model["gradient_l2"] <= 1e-4
    assert model["objective_gap_upper_bound"] <= 2.5e-10
    for r in source[:4]:
        assert 0 < replay.selector_probability(r, 0, model) < 1
        assert replay.selector_probability(r, 0, {"status": "insufficient_inner_history"}) == r["q"][0]


def test_failed_selector_solver_never_silently_excludes_scope(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def failed(fun, start, **kwargs):
        calls.append(kwargs["method"])
        assert not np.any(start)
        return SimpleNamespace(x=np.full(len(start), 10.), success=False, message="forced failure")
    monkeypatch.setattr(replay, "minimize", failed)
    with pytest.raises(RuntimeError, match="selector fit failed"):
        replay.fit_selector(rows(), protocol(), True)
    assert calls == ["trust-exact", "L-BFGS-B"]


def test_outcomes_join_after_policy_and_void_not_a_loss():
    p, source = protocol(), rows(1)
    r = source[0]; r.update(day="2025-01-01", kickoff="2025-01-01T14:00:00")
    setting = {"blend": {"alpha": .25}, "selector_market": {"status": "missing"},
               "selector_score": {"status": "missing"}}
    options, labels = replay.make_options(source, {"bs|NPB|승패|2": setting})
    changed = deepcopy(source); changed[0].update(winner=None, is_void=True)
    other_options, other_labels = replay.make_options(changed, {"bs|NPB|승패|2": setting})
    assert options == other_options
    assert all(not {"hit", "winner", "result", "profit", "is_void"} & set(o)
               for pool in options.values() for o in pool)
    actual = replay.run_policy(options)
    assert actual["strategies"]["market"]["highlighted"] == ["0:0"]
    assert labels["0:0"]["hit"] == 0
    assert other_labels["0:0"]["profit"] == 0
    assert other_labels["0:0"]["hit"] is None


def test_void_retained_before_selection_when_price_known():
    record = dict(year=2025, round=2, date_text="01.03(금) 17:00", sport="bs", league="NPB",
                  market_family="핸디캡", market_label="H -1.5", booking_class="2-way", n_way=2,
                  home="A", away="B", odds="1.8,1.8", result="취소", is_void=True)
    p = protocol()
    assert normalize(pd.DataFrame([record]), p)[0] == []
    retained = normalize(pd.DataFrame([record]), p, include_void=True)[0]
    assert len(retained) == 1 and retained[0]["winner"] is None
    assert retained[0]["market_label"] == "H -1.5"


@pytest.mark.parametrize("market,label,expected", [("언더오버", "U 2.25", False),
    ("핸디캡", "H -1.75", False), ("핸디캡", "H -1.5", True),
    ("언더오버", "U -2.5", False), ("언더오버", "U 2.5", True)])
def test_supported_lines_exclude_quarter_stakes_before_forecasting(market, label, expected):
    assert replay.supported_score_offer({"market": market, "market_label": label}) is expected


def test_incremental_score_selector_comparison_is_corrected():
    p = protocol(); p["bootstrap_repeats"] = 50
    source = rows(4)
    for i, r in enumerate(source):
        r.update(day=f"2025-01-{i*7+1:02d}", kickoff=f"2025-01-{i*7+1:02d}T14:00:00")
    setting = {"blend": {"alpha": .1}, "selector_market": {"status": "missing"},
               "selector_score": {"status": "missing"}}
    options, outcomes = replay.make_options(source, {"bs|NPB|승패|2": setting})
    policy = replay.run_policy(options)
    report = replay.aggregate(source, policy, outcomes, p)
    assert report["comparisons_count"] == 27
    for item in report["scopes"].values():
        assert item["score_information_control"]["holm_p"] == 1
        assert not item["comparisons"]["selector_score"]["historical_signal"]


def test_summary_denominators_include_void_refund_but_not_hit():
    data = [pick(0, hit=1), pick(1, hit=0), pick(2, hit=None, profit=0, void=True)]
    result = replay.summary(data, {r["day"] for r in data})
    assert result["picks"] == 3 and result["graded"] == 2 and result["accuracy"] == .5
    assert result["roi"] == pytest.approx((.6-1)/3)
    assert replay.summary([], {"2025-01-01"})["accuracy"] is None


def test_paired_bootstrap_identity_constant_gain_reproducible():
    p = protocol(); p["bootstrap_repeats"] = 1000
    ref = [pick(i, day=(pd.Timestamp("2025-01-01")+pd.Timedelta(days=i*7)).date().isoformat(), hit=0)
           for i in range(50)]
    same = replay.compare_sets(ref, ref, p, 42)
    assert same["gain"] == [0, 0, 0] and same["p_two_sided"] == 1
    other = [dict(r, hit=1, profit=r["odds"]-1) for r in ref]
    result = replay.compare_sets(other, ref, p, 42)
    assert result == replay.compare_sets(other, ref, p, 42)
    assert result["gain"][0] == 1 and result["ci95"][0] == [1, 1]
    assert replay.compare_sets([], ref, p, 42)["gain"] is None
    one_week = replay.compare_sets([pick(0, hit=1)], [pick(1, hit=0)], p, 42)
    assert one_week["gain"][0] == 1 and one_week["ci95"] is None
    assert one_week["p_two_sided"] is None


def test_matched_control_preserves_strata_count_and_ignores_outcomes():
    p = protocol()
    candidate = [pick(0, odds=1.62), pick(1, odds=1.75)]
    reference = [pick(5, day=candidate[0]["day"], odds=1.65),
                 pick(6, day=candidate[1]["day"], odds=1.78),
                 pick(7, day=candidate[0]["day"], odds=1.55),
                 pick(8, day=candidate[0]["day"], odds=1.63, league="MLB")]
    a, b = replay.matched_reference(candidate, reference, p)
    assert [r["id"] for r in b] == ["5", "6"] and len(a) == len(b)
    changed = [dict(r, hit=1-r["hit"], profit=100) for r in reference]
    assert [r["id"] for r in replay.matched_reference(candidate, changed, p)[1]] == ["5", "6"]


def test_quarter_fit_cutoff_and_predictions_independent_of_outer_outcomes(monkeypatch):
    p, source = protocol(), rows(2)
    for i, r in enumerate(source):
        r.update(day=f"2025-01-{i+1:02d}", kickoff=f"2025-01-{i+1:02d}T14:00:00")
    calls = []
    class Model:
        metadata = {"status": "test"}
        def predict(self, row): return [.7, .3]
    def fit(matches, cutoff, **kwargs):
        calls.append(cutoff)
        return Model()
    monkeypatch.setattr(replay, "fit_score_model", fit)
    key, output, folds = replay.quarterly_predictions((("bs", "NPB"), source, [], p))
    assert calls == [pd.Timestamp("2024-12-25")]
    assert all(r["score_p"] == [.7, .3] for r in output)
    assert len(output) == 2


def test_unpriceable_prediction_fails_instead_of_dropping_rows(monkeypatch):
    class Model:
        metadata = {}
        def predict(self, row): return None
    monkeypatch.setattr(replay, "fit_score_model", lambda *args, **kwargs: Model())
    with pytest.raises(RuntimeError, match="Unpriceable"):
        replay.quarterly_predictions((("bs", "NPB"), rows(1), [], protocol()))


def test_quarters_cover_every_calendar_day_including_march_june_september_december(monkeypatch):
    p, source = protocol(), rows(608)
    for i, r in enumerate(source):
        day = pd.Timestamp("2025-01-01")+pd.Timedelta(days=i)
        r.update(day=day.date().isoformat(), kickoff=day.isoformat())
    monkeypatch.setattr(replay, "fit_score_model", lambda *args, **kwargs: None)
    _, output, folds = replay.quarterly_predictions((("bs", "NPB"), source, [], p))
    expected = [r for r in source if r["day"] <= p["evaluation_end"]]
    assert [r["offer"] for r in output] == [r["offer"] for r in expected]
    assert len({r["offer"] for r in output}) == len(expected)
    assert folds[0]["end_exclusive"] == "2025-04-01"
