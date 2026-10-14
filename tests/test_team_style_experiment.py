from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from team_style_archive import digest, normalize, holm, diagnostic
from team_style_experiment import CANDIDATES, design, fit, predict, run_league, validate_protocol, verify_unchanged
from team_style_features import build_styles, feature_columns, score_history, profile
import team_style_experiment as runner


def protocol():
    return json.loads((Path(__file__).resolve().parents[1]/"experiments/team-style-20260907/protocol.json").read_text(encoding="utf-8"))


def match(day, home="A", away="B", hs=3, aws=1, league="NPB"):
    time = pd.Timestamp(day)
    return dict(kickoff=time, home_team=home, away_team=away, home_score=hs, away_score=aws,
                league=league, sport="bs", event=digest((time.isoformat(), "bs", league, home, away)))


def archive(**kwargs):
    return dict(year=2025, round=2, date_text="01.03(금) 17:00", sport="bs", league="NPB",
                market_family="승패", market_label="", booking_class="2-way", n_way=2,
                home="A 3", away="1 B", odds="1.8,1.8", result="홈승", is_void=False, **kwargs)


def offer(i=0, league="NPB", winner=0):
    m = match(f"2024-01-{i%28+1:02d}", league=league)
    return {"day": m["kickoff"].date().isoformat(), "kickoff": m["kickoff"].isoformat(),
            "sport": "bs", "league": league, "market": "승패", "n_way": 2,
            "q": [.55, .45], "winner": winner, "odds": [1.7, 2.0],
            "features": {"home_gf5": float(i%4)}, "offer": str(i), "event": m["event"],
            "home_team": "A", "away_team": "B"}


def test_score_loader_dedup_doubleheader_conflict():
    a = archive()
    later = {**a, "date_text": "01.03(금) 20:00"}
    rows, quality = score_history(pd.DataFrame([a, a, later]))
    assert len(rows) == 2
    bad = {**a, "home": "A 4"}
    rows, quality = score_history(pd.DataFrame([a, bad, later]))
    assert len(rows) == 1 and quality["score_conflicts"] == 1


@pytest.mark.parametrize("change", [
    {"market_family": "핸디캡"}, {"market_family": "전반승패"}, {"market_label": "h(전반)"},
    {"home": "A -3"}, {"home": "A 1.5"}, {"is_void": "true"}, {"result": "홈패"}])
def test_reject_non_fulltime_or_invalid_score(change):
    assert not score_history(pd.DataFrame([{**archive(), **change}]))[0]


def test_same_day_and_future_results_cannot_change_features():
    matches = [match("2024-01-01"), match("2024-01-02 14:00"), match("2024-01-02 20:00"), match("2024-01-03")]
    old, _ = build_styles(matches)
    changed = deepcopy(matches)
    changed[1]["home_score"] = 100
    changed[2]["away_score"] = 100
    new, _ = build_styles(changed)
    for r in matches[:3]:
        assert old[r["event"]] == new[r["event"]]
    assert old[matches[3]["event"]] != new[matches[3]["event"]]


def test_input_order_and_other_league_independence():
    a = [match(f"2024-01-{i+1:02d}", hs=i%6) for i in range(12)]
    b = [match(f"2024-01-{i+1:02d}", hs=100+i, league="MLB") for i in range(12)]
    expected, _ = build_styles(a)
    actual, _ = build_styles(list(reversed(a+b)))
    assert all(actual[k] == v for k, v in expected.items())


def test_duplicate_events_fail_closed():
    m = match("2024-01-01")
    with pytest.raises(ValueError):
        build_styles([m, m])


def test_constant_scoring_variance_zero_not_margin_size():
    rows = [match(f"2024-01-{i+1:02d}", hs=10, aws=1) for i in range(9)]
    features, _ = build_styles(rows)
    last = features[rows[-1]["event"]]
    assert last["home_margin_sd20"] == 0
    assert last["home_gf_sd20"] == 0
    assert last["home_season_margin"] > 0


def test_missing_is_not_zero_and_shrink_small_opponent_samples():
    day = pd.Timestamp("2024-01-10")
    p = profile([], day, 2024, "home", None, 1500, 1500)
    assert p["gf5"] is None and p["history_n"] == 0
    hist = [{"day": day-pd.Timedelta(days=1), "season": 2024, "venue": "home",
             "gf": 5, "ga": 2, "attack_residual": 1., "defence_residual": 0.,
             "opponent_strength": .5, "win_residual": .6}]
    p = profile(hist, day, 2024, "home", 3, 1500, 1500)
    assert p["strong_n"] == 1 and p["strong_residual"] == pytest.approx(.6/9)
    assert p["weak_residual"] is None and p["margin_sd20"] is None


def test_opponent_slope_distinguishes_strong_vs_weak():
    day = pd.Timestamp("2024-02-01")
    hist = [{"day": day-pd.Timedelta(days=20-i), "season": 2024, "venue": "home",
             "gf": 3, "ga": 1, "attack_residual": 0., "defence_residual": 0.,
             "opponent_strength": x, "win_residual": .4*x}
            for i, x in enumerate([-1., 1.]*10)]
    p = profile(hist, day, 2024, "home", 3, 1500, 1500)
    assert p["strength_slope"] > 0 and p["strong_residual"] > 0 and p["weak_residual"] < 0


def test_cross_year_season_and_source_year():
    rows = [match("2024-12-31", league="NBA"), match("2025-01-01", league="NBA")]
    f, _ = build_styles(rows)
    assert f[rows[1]["event"]]["home_season_margin"] > 0
    raw = {**archive(), "year": 2025, "round": 1, "date_text": "12.31(화) 17:00"}
    assert score_history(pd.DataFrame([raw]))[0][0]["kickoff"].year == 2024


def test_join_key_and_no_runtime_database_access(monkeypatch):
    monkeypatch.setenv("PROODD_DB_ENABLED", "1")
    raw = pd.DataFrame([archive()])
    rows, _ = normalize(raw, protocol())
    scores, _ = score_history(raw)
    assert rows[0]["event"] == scores[0]["event"]


def test_design_uses_training_state():
    train = [offer(i) for i in range(8)]
    _, state = design(train, ["home_gf5"])
    before = deepcopy(state)
    test = offer(); test["features"]["home_gf5"] = 999
    x, after = design([test], ["home_gf5"], state)
    assert before == after and x[0, 1] == 5


def test_ablation_removes_dependent_interaction():
    cols = feature_columns(CANDIDATES["no_opponent"])
    assert not any("strength" in c or "strong" in c or "weak" in c for c in cols)
    cols = feature_columns(CANDIDATES["no_scoring"])
    assert not any("interaction" in c or "elo" in c for c in cols)


def test_zero_coefficients_reproduce_full_market_vector():
    rows = [offer(i, winner=i%2) for i in range(12)]
    model = fit(rows, "2025-01-01", "scoring", 100)
    model["beta"] = np.zeros_like(model["beta"]).tolist()
    np.testing.assert_allclose(predict(rows, model), [r["q"] for r in rows])


def test_fit_scope_cutoff_and_draw_support():
    rows = [offer(i, winner=i%2) for i in range(12)]
    with pytest.raises(ValueError):
        fit(rows+[offer(0, "MLB")], "2025-01-01", "control", 100)
    with pytest.raises(ValueError):
        fit(rows, "2024-01-02", "control", 100)
    three = [{**r, "market": "승무패", "n_way": 3, "q": [.4, .3, .3], "winner": i%3}
             for i, r in enumerate(rows)]
    model = fit(three, "2025-01-01", "control", 100)
    np.testing.assert_allclose(predict(three, model).sum(axis=1), 1.)
    with pytest.raises(ValueError):
        predict([{**three[0], "league": "MLB"}], model)


def test_quarter_labels_do_not_change_same_quarter_fit():
    p = protocol(); p.update(minimum_training_markets=5, bootstrap_repeats=20, evaluation_end="2025-01-31")
    train = [offer(i, winner=i%2) for i in range(12)]
    test = [{**offer(i), "day": f"2025-01-{i+1:02d}", "kickoff": f"2025-01-{i+1:02d}T00:00:00"} for i in range(3)]
    _, a = run_league((train+test, p))
    _, b = run_league((train+[{**r, "winner": 1-r["winner"]} for r in test], p))
    assert a["folds"] == b["folds"]
    assert a["folds"][0]["cutoff_exclusive"] == "2024-12-25"


def test_protocol_and_holm():
    p = protocol(); validate_protocol(p)
    p["production_allowed"] = True
    with pytest.raises(ValueError): validate_protocol(p)
    assert holm({"a": .01, "b": .04, "c": None}) == {"a": .02, "b": .04, "c": None}


def test_calendar_gaps_are_not_active_evidence_weeks():
    p = protocol(); p["bootstrap_repeats"] = 20
    rows = [{"day": day, "metrics": {"baseline": {"brier": .4, "log_loss": .7},
                                      "full": {"brier": .3, "log_loss": .6}}}
            for day in ("2025-01-01", "2025-12-31")]
    d = diagnostic(rows, "full", p, 42)
    assert d["active_weeks"] == 2 and d["calendar_weeks"] == 53


@pytest.mark.parametrize("change", [{"market_label": "h(전반)"}, {"booking_class": "first-half"}])
def test_normalize_rejects_period_variant(change):
    rows, quality = normalize(pd.DataFrame([archive(), {**archive(), **change}]), protocol())
    assert len(rows) == 1
    assert quality["excluded_or_duplicates"]["unsupported_period_or_booking"] == 1


def test_changed_source_or_code_invalidates_run():
    verify_unchanged({"code": "abc"}, {"code": "abc"})
    with pytest.raises(RuntimeError):
        verify_unchanged({"code": "abc"}, {"code": "def"})


def test_main_refuses_output_when_code_changes_during_run(tmp_path, monkeypatch):
    source = tmp_path/"games.csv"
    source.write_text("x\n1\n", encoding="utf-8")
    p = tmp_path/"protocol.json"
    p.write_text(json.dumps(protocol()), encoding="utf-8")
    output = tmp_path/"report.json"
    hashes = iter([{"module": "before"}, {"module": "after"}])
    monkeypatch.setattr(runner, "code_hashes", lambda: next(hashes))
    monkeypatch.setattr(runner, "experiment", lambda *args: {})
    monkeypatch.setattr(sys, "argv", ["experiment", "--games", str(source),
                                      "--protocol", str(p), "--output", str(output)])
    with pytest.raises(RuntimeError, match="changed during experiment"):
        runner.main()
    assert not output.exists()
    assert source.read_text(encoding="utf-8") == "x\n1\n"
