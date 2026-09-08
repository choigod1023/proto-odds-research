from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import nonlinear_accuracy_lab as lab

PROTOCOL = json.loads((lab.ROOT / "experiments/nonlinear-accuracy-20260908/protocol.json")
                      .read_text(encoding="utf-8"))


def row(i, **kw):
    base = dict(row_id=str(i), event_key=f"e{i}", kickoff=pd.Timestamp("2025-01-01 12:00"),
        sport="bs", league="KBO", market="승패", market_label="", n_way=2,
        sel="홈승", odds=1.6, q=.6, y=1., is_void=False,
        catboost_market=.61, catboost_team=.62, validated_team=.6, model_available=True)
    base.update(kw)
    return base


def frame(*rows):
    return pd.DataFrame(rows)


def protocol(**kw):
    p = deepcopy(PROTOCOL)
    p["bootstrap_repeats"] = 100
    p.update(kw)
    return p


def test_protocol_rejects_promotion_and_overlapping_dates():
    lab.validate_protocol(PROTOCOL)
    with pytest.raises(ValueError):
        lab.validate_protocol(protocol(production_allowed=True))
    with pytest.raises(ValueError):
        lab.validate_protocol(protocol(selection_cutoff_exclusive="2025-02-01"))
    with pytest.raises(ValueError):
        lab.validate_protocol(protocol(blend_grid=[.25, 1]))
    with pytest.raises(ValueError):
        lab.validate_protocol(protocol(blend_grid=[0, float("nan")]))


def test_training_embargo_and_voids():
    data = frame(
        row(0, kickoff=pd.Timestamp("2024-12-24 23:59")),
        row(1, kickoff=pd.Timestamp("2024-12-25")),
        row(2, kickoff=pd.Timestamp("2025-01-01")),
        row(3, kickoff=pd.Timestamp("2024-11-01"), y=np.nan, is_void=True),
        row(4, kickoff=pd.Timestamp("2022-12-31")),
    )
    assert lab.training_subset(data, "2025-01-01", PROTOCOL).row_id.tolist() == ["0"]


def test_inner_selection_cannot_see_evaluation_labels():
    dates = pd.date_range("2024-01-01", periods=400, freq="D")
    data = pd.DataFrame([row(i, kickoff=d, y=float(i % 3 != 0)) for i, d in enumerate(dates)])
    p = protocol(minimum_validation_rows=10, minimum_validation_weeks=2)
    before = lab.select_blend(data, p)
    data.loc[data.kickoff >= "2024-12-25", ["y", "catboost_team"]] = [0, .99]
    assert lab.select_blend(data, p) == before
    assert before["rows"] == 359


def test_blend_market_fallback_does_not_drop_rows():
    data = frame(row(1, kickoff=pd.Timestamp("2024-01-03"), model_available=False))
    assert lab.select_blend(data, PROTOCOL)["alpha"] == 0


def test_void_refund_and_accuracy_denominator():
    data = frame(row(1, odds=2, y=1), row(2, odds=1.6, y=0),
                 row(3, odds=1.8, y=np.nan, is_void=True))
    m = lab.metrics(data, "market")
    assert (m["picks"], m["settled"], m["wins"], m["voids"]) == (3, 2, 1, 1)
    assert m["hit_rate"] == .5
    assert m["flat_single_roi"] == 0


def test_payload_does_not_contain_results_or_features():
    payload = lab.policy_payload(frame(row(1, home_score=99, arbitrary_feature=2)), "catboost_team")[0]
    assert payload["predicted_hit_prob"] == .62
    assert payload["kickoff_at"] == "2025-01-01T12:00:00+09:00"
    assert not {"y", "winner", "result", "is_void", "home_score", "arbitrary_feature"} & payload.keys()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_real_policy_per_game_league_and_odds_preferences():
    data = frame(
        row(1, event_key="one", odds=1.4, q=.75),
        row(2, event_key="one", market="언더오버", market_label="U/O 8.5", odds=1.65, q=.56),
        row(3, q=.54),  # no quota filling below 55%
        row(4, league="NPB", q=.58),
    )
    choices, selected = lab.select_policy(data, "market")
    assert set(choices.row_id) == {"2", "3", "4"}
    assert set(selected.row_id) == {"2", "4"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_bridge_rejects_outcomes_and_invalid_probability():
    payload = lab.policy_payload(frame(row(1)), "market")
    payload[0]["winner"] = 0
    command = ["node", str(lab.ROOT / "scripts/nonlinear_policy_bridge.mjs")]
    run = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, encoding="utf-8")
    assert run.returncode != 0
    del payload[0]["winner"]
    payload[0]["predicted_hit_prob"] = 1.2
    run = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, encoding="utf-8")
    assert run.returncode != 0


def test_matching_is_blind_to_outcomes_and_preserves_buckets():
    candidate = frame(row(1, q=.65), row(2, odds=1.75), row(3, league="MLB"))
    reference = frame(row(4, q=.59), row(5, q=.63), row(6, odds=1.75), row(7, league="NPB"))
    a, b = lab.matched_picks(candidate, reference)
    assert a.row_id.tolist() == ["1", "2"]
    assert b.row_id.tolist() == ["5", "6"]
    candidate["y"], reference["y"] = 0, 0
    c, d = lab.matched_picks(candidate, reference)
    assert c.row_id.tolist() == a.row_id.tolist()
    assert d.row_id.tolist() == b.row_id.tolist()


def test_calendar_keeps_empty_weeks_and_boundary():
    first, n, samples = lab.calendar_resamples("2025-01-01", "2025-03-01", protocol())
    assert first == pd.Timestamp("2024-12-30")
    assert n == 9
    assert samples.shape == (100, 9)
    assert samples.min() == 0
    assert samples.max() == 8


def test_identical_strategy_has_zero_difference_and_p_one():
    data = frame(*[row(i, kickoff=pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                           y=float(i % 2)) for i in range(30)])
    result = lab.comparison(data, data, protocol())
    assert result == {"delta": 0., "ci95": [0., 0.], "p": 1.}


def test_holm_is_joint_monotone_and_retains_missing():
    rows = [{"p": .01}, {"p": None}, {"p": .02}, {"p": .8}]
    lab.holm(rows)
    assert [x.get("holm_p") for x in rows] == [.03, None, .04, .8]


def test_fit_group_separates_future_and_includes_last_quarter_month(monkeypatch):
    p = protocol(training_start="2023-01-01", inner_start="2024-01-01",
        minimum_training_rows=2, minimum_training_class=1,
        minimum_validation_rows=10000)
    data = frame(
        row(1, kickoff=pd.Timestamp("2023-03-01"), y=0),
        row(2, kickoff=pd.Timestamp("2023-04-01"), y=1),
        row(3, kickoff=pd.Timestamp("2024-03-31"), y=0),
        row(4, kickoff=pd.Timestamp("2025-03-31"), y=1),
        row(5, kickoff=pd.Timestamp("2026-08-25 23:00"), y=1),
        row(6, kickoff=pd.Timestamp("2026-08-26"), y=1))
    calls = []
    def fake_fit(train, test, columns, protocol, cutoff):
        assert train.kickoff.max() < cutoff - pd.Timedelta(days=7)
        calls.append((train.row_id.tolist(), test.row_id.tolist()))
        return test.q.to_numpy() + .01
    monkeypatch.setattr(lab, "fit_predict", fake_fit)
    prediction, info = lab.fit_group((data, p, ["q"], []))
    assert prediction.row_id.tolist() == ["4", "5"]
    assert (prediction.validated_team == prediction.q).all()
    assert info["blend"]["alpha"] == 0
    assert calls


def test_market_baseline_added_once_with_real_catboost():
    catboost = pytest.importorskip("catboost")
    p = protocol(iterations=3, depth=2)
    data = frame(*[row(i, kickoff=pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                      q=.4 + .2 * (i % 2), y=float(i % 3 != 0)) for i in range(40)])
    got = lab.fit_predict(data, data, ["q"], p, pd.Timestamp("2024-03-01"))
    assert got.shape == (40,)
    assert ((got > 0) & (got < 1)).all()
    # A zero-residual tree must recover q, not 0.5 or sigmoid(2*logit(q)).
    np.testing.assert_allclose(lab.sigmoid(lab.logit(data.q)), data.q)


def test_cli_refuses_existing_report_before_training(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["lab", "--games", "absent.csv",
        "--protocol", "absent.json", "--output", str(output)])
    with pytest.raises(FileExistsError):
        lab.main()
    assert output.read_text() == "preserve"


def test_cli_rejects_non_json_output(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lab", "--games", "absent.csv",
        "--protocol", "absent.json", "--output", str(tmp_path / "report.md")])
    with pytest.raises(ValueError):
        lab.main()

def test_catboost_offset_contract_with_controlled_residual(monkeypatch):
    from types import SimpleNamespace
    data = frame(row(1, q=.3), row(2, q=.75))
    observed = {}
    class Pool:
        def __init__(self, x, **kw):
            self.x, self.kw = x, kw
    class Model:
        def __init__(self, **kw):
            observed["params"] = kw
        def fit(self, pool):
            np.testing.assert_allclose(pool.kw["baseline"], lab.logit(data.q))
            assert pool.kw["label"].tolist() == [1, 1]
            observed["fit"] = True
        def predict(self, pool, **kw):
            assert "baseline" not in pool.kw
            assert kw["prediction_type"] == "RawFormulaVal"
            return np.full(len(pool.x), .2)
    monkeypatch.setitem(sys.modules, "catboost", SimpleNamespace(CatBoostClassifier=Model, Pool=Pool))
    result = lab.fit_predict(data, data, ["q"], protocol(), pd.Timestamp("2025-01-02"))
    np.testing.assert_allclose(result, lab.sigmoid(lab.logit(data.q) + .2))
    assert observed["fit"] is True
    assert observed["params"]["allow_writing_files"] is False
    assert observed["params"]["thread_count"] == 1


def test_void_only_weeks_cannot_satisfy_evidence_gate(monkeypatch):
    # Even perfect-looking mocked significance must not override temporal coverage.
    p = protocol()
    baseline, candidate = [], []
    for year in (2025, 2026):
        for i in range(300):
            day = pd.Timestamp(f"{year}-01-02")
            baseline.append(row(f"b{year}-{i}", kickoff=day, y=float(i < 150)))
            candidate.append(row(f"a{year}-{i}", kickoff=day, y=float(i < 270)))
    for i, day in enumerate(pd.date_range("2025-02-01", periods=30, freq="7D")):
        baseline.append(row(f"bv{i}", kickoff=day, y=np.nan, is_void=True))
        candidate.append(row(f"av{i}", kickoff=day, y=np.nan, is_void=True))
    b, a = pd.DataFrame(baseline), pd.DataFrame(candidate)
    monkeypatch.setattr(lab, "comparison", lambda *args: {
        "delta": .4, "ci95": [.3, .5], "p": .000001})
    selected = {s: b if s == "market" else a for s in lab.STRATEGIES}
    summaries, _ = lab.scope_summaries(pd.concat([a, b]), selected, selected, p)
    overall = summaries[0]
    assert overall["metrics"]["market"]["weeks"] == 32
    assert overall["metrics"]["market"]["settled_weeks"] == 2
    assert not any(c["historical_signal"] for c in overall["comparisons"])
