"""Daily risk limits and cross-runtime contract; no DB or pipeline execution."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daily_value import (  # noqa: E402
    BASE_PER_LEAGUE, MIN_HIT, MIN_RETURN, POLICY_VERSION,
    annotate_daily_values, daily_value_metrics,
)


def pick(game_no="1", **changes):
    return {
        "round": 105, "game_no": game_no, "market": "승패", "market_label": "",
        "sel": "홈", "odds": 1.6, "market_prob": 0.6, "league": "KBO",
        "kickoff_at": "2026-09-05T18:00:00+09:00", **changes,
    }


def validated(game_no="1", **changes):
    return pick(game_no, **{
        "predicted_hit_prob": 0.7, "decision_pipeline_applied": True,
        "has_validated_edge": True, "validated_uncertainty_available": True,
        "uncertainty_source": "validated_residual_interval",
        "probability_interval": [0.65, 0.75], "probability_lower_bound": 0.65,
        **changes,
    })


def decisions(rows):
    return [row["daily_recommendation"] for row in annotate_daily_values(rows)]


def test_formula_uses_unrounded_candidate_price_not_historical_roi():
    row = pick(odds=1.73123456, market_prob=0.573456789, hist_roi=9, hist_n=999999)
    result = daily_value_metrics(row)
    assert result == {
        "policy_version": "daily-value-v1", "probability": row["market_prob"],
        "comparison_probability": row["market_prob"],
        "break_even_probability": 1 / row["odds"],
        "expected_return": row["market_prob"] * row["odds"] - 1,
        "comparison_return": row["market_prob"] * row["odds"] - 1,
        "validated_probability": False, "validated_interval": False,
        "qualifies": True,
    }
    assert (POLICY_VERSION, MIN_HIT, MIN_RETURN, BASE_PER_LEAGUE) == (
        "daily-value-v1", 0.50, -0.15, 3,
    )


def test_null_and_container_values_cannot_become_probabilities():
    assert daily_value_metrics(None)["qualifies"] is False
    for value in [[], [0.6], {"value": 0.6}]:
        assert daily_value_metrics(pick(market_prob=value))["probability"] is None
    result = daily_value_metrics(validated(probability_interval=[[0.65], 0.75]))
    assert result["validated_interval"] is False
    assert result["comparison_probability"] == 0.6


@pytest.mark.parametrize("changes", [
    {"decision_pipeline_applied": False}, {"has_validated_edge": False},
    {"decision_pipeline_applied": 1}, {"has_validated_edge": "true"},
    {"predicted_hit_prob": None}, {"predicted_hit_prob": 0},
    {"predicted_hit_prob": 1}, {"predicted_hit_prob": True},
    {"predicted_hit_prob": float("nan")}, {"predicted_hit_prob": float("inf")},
])
def test_unvalidated_or_invalid_final_falls_back_to_required_market(changes):
    result = daily_value_metrics(validated(**changes))
    assert result["probability"] == result["comparison_probability"] == 0.6
    assert not result["validated_probability"]
    assert not result["validated_interval"]


def test_fake_high_final_without_markers_cannot_pass_hit_floor():
    result = decisions([pick(market_prob=0.49, predicted_hit_prob=0.99)])[0]
    assert result["probability"] == 0.49
    assert result["reason_code"] == "hit_floor"


@pytest.mark.parametrize("field", ["odds", "market_prob"])
@pytest.mark.parametrize("value", [None, "", "bad", True, False, 0, 1,
                                  float("nan"), float("inf"), -float("inf")])
def test_invalid_required_numbers_have_complete_json_null_contract(field, value):
    result = daily_value_metrics(validated(**{field: value}))
    assert result["policy_version"] == POLICY_VERSION
    assert all(result[key] is None for key in (
        "probability", "comparison_probability", "break_even_probability",
        "expected_return", "comparison_return",
    ))
    assert all(result[key] is False for key in (
        "validated_probability", "validated_interval", "qualifies",
    ))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("changes", [
    {"validated_uncertainty_available": False},
    {"validated_uncertainty_available": 1},
    {"uncertainty_source": "shin_market_fallback"},
    {"probability_interval": None}, {"probability_interval": "0.65,0.75"},
    {"probability_interval": (0.65, 0.75)},
    {"probability_interval": []}, {"probability_interval": [0.65]},
    {"probability_interval": [0.65, 0.75, 0.8]},
    {"probability_interval": [0, 0.75]}, {"probability_interval": [0.65, 1]},
    {"probability_interval": [0.72, 0.75]},
    {"probability_interval": [0.65, 0.69]},
    {"probability_interval": [0.75, 0.65]},
    {"probability_interval": [None, 0.75]},
    {"probability_interval": [0.65, True]},
    {"probability_interval": [0.65, float("nan")]},
    {"probability_interval": [float("inf"), 0.75]},
    {"probability_lower_bound": None}, {"probability_lower_bound": 0.6},
    {"probability_lower_bound": 0.65006},
])
def test_interval_requires_full_validated_array_and_matching_bound(changes):
    result = daily_value_metrics(validated(**changes))
    assert result["validated_probability"] is True
    assert result["validated_interval"] is False
    assert result["probability"] == 0.7
    assert result["comparison_probability"] == 0.6


@pytest.mark.parametrize("changes", [
    {}, {"probability_lower_bound": 0.6500000005},
    {"predicted_hit_prob": 0.65}, {"predicted_hit_prob": 0.75},
    {"predicted_hit_prob": 0.65, "probability_interval": [0.65, 0.65]},
])
def test_valid_interval_includes_estimate_endpoints_and_bound_tolerance(changes):
    result = daily_value_metrics(validated(**changes))
    assert result["validated_interval"] is True
    assert result["comparison_probability"] == 0.65
    assert result["comparison_return"] == 0.65 * 1.6 - 1


def test_conservative_fallback_uses_lower_of_market_and_validated_estimate():
    result = daily_value_metrics(validated(predicted_hit_prob=0.55))
    assert result["validated_probability"] is True
    assert result["validated_interval"] is False
    assert result["probability"] == result["comparison_probability"] == 0.55


@pytest.mark.parametrize("probability,odds,qualifies,reason", [
    (0.5, 1.7, True, "base"),
    (0.5 - 1e-13, 1.8, False, "hit_floor"),
    (0.5, 1.7 - 1e-12, True, "base"),
    (0.5, 1.7 - 4e-12, False, "return_floor"),
])
def test_hit_floor_is_exact_and_return_floor_has_epsilon(probability, odds, qualifies, reason):
    result = decisions([pick(market_prob=probability, odds=odds)])[0]
    assert result["qualifies"] is qualifies
    assert result["recommended"] is qualifies
    assert result["reason_code"] == reason


@pytest.mark.parametrize("changes,reason", [
    ({"market": " 홀짝 "}, "safety"), ({"odds": 2.2}, "safety"),
    ({"odds": None}, "safety"), ({"odds": True}, "safety"),
    ({"is_market_favorite": False}, "safety"),
    ({"final_reversal": True}, "safety"), ({"최종전환": True}, "safety"),
    ({"market_prob": None}, "invalid"), ({"market_prob": True}, "invalid"),
    ({"market_prob": 0.49}, "hit_floor"), ({"odds": 1.1}, "return_floor"),
])
def test_excluded_candidates_keep_metrics_and_first_reason(changes, reason):
    row = pick(**changes)
    result = annotate_daily_values([row])[0]
    assert {key: value for key, value in result.items() if key != "daily_recommendation"} == row
    assert result["daily_recommendation"]["reason_code"] == reason
    assert result["daily_recommendation"]["recommended"] is False
    assert result["daily_recommendation"]["league_rank"] is None


def test_aliases_are_nullish_fallbacks_in_metrics_and_stable_key():
    rows = [pick(game_no=None, 게임번호=str(i), odds=None, 배당="1.6",
                 market_prob=None, 시장확률="0.6", sel=None, 선택="홈")
            for i in [4, 2, 1, 3]]
    assert [row["league_rank"] for row in decisions(rows)] == [4, 2, 1, 3]
    assert daily_value_metrics(pick(odds="", 배당=1.6))["probability"] is None
    assert daily_value_metrics(pick(market_prob=False, 시장확률=0.6))["probability"] is None


def test_low_odds_fallback_only_when_no_qualifying_primary_in_day_league():
    low = pick("low", odds=1.4, market_prob=0.7)
    primary = pick("primary", odds=1.7, market_prob=0.51)
    assert [row["reason_code"] for row in decisions([low, primary])] == ["fallback", "base"]
    failing_primary = pick("fail", odds=1.5, market_prob=0.51)
    assert [row["reason_code"] for row in decisions([low, failing_primary])] == ["base", "return_floor"]
    assert decisions([low, {**primary, "league": "NPB"}])[0]["recommended"]
    assert decisions([low, {**primary, "kickoff_at": "2026-09-06T18:00:00+09:00"}])[0]["recommended"]


def test_each_kst_day_and_league_has_own_cap_and_utc_rolls_forward():
    rows = [pick(str(i), kickoff_at=stamp, league=league)
            for stamp in ["2026-09-05T15:30:00Z", "2026-09-07T00:30:00+09:00"]
            for league in ["KBO", "NPB"] for i in range(4)]
    result = decisions(rows)
    assert sum(row["recommended"] for row in result) == 12
    same_day = [pick(str(i), kickoff_at=("2026-09-05T15:30:00Z" if i % 2
                                        else "2026-09-06T00:30:00+09:00"))
                for i in range(4)]
    assert sum(row["recommended"] for row in decisions(same_day)) == 3


def test_legacy_dates_and_undated_groups_never_infer_clock_year():
    rows = [pick(str(i), kickoff_at="invalid", date="09.05(토) 18:00", **year)
            for year in [{"year": 2026}, {"year": 2027}, {}] for i in range(4)]
    rows += [pick(str(i), kickoff_at=None, date=None) for i in range(4)]
    assert sum(row["recommended"] for row in decisions(rows)) == 12
    iso = [pick(str(i)) for i in range(3)]
    assert decisions(iso + [pick("9", kickoff_at=None, date="09.05", year=2026)])[-1]["league_rank"] == 1
    assert decisions(iso + [pick("9", kickoff_at=None, date="09.05")])[-1]["league_rank"] == 1
    # The explicit-year fallback shares the ISO group (only three selected).
    assert sum(row["recommended"] for row in decisions(
        iso + [pick("9", kickoff_at=None, date="09.05", year=2026)])) == 3


def test_beyond_three_requires_validated_interval_and_strictly_positive_return():
    base = [pick(str(i), odds=2, market_prob=0.8) for i in range(3)]
    extras = [
        validated("extra"),
        validated("no-interval", probability_interval=None),
        pick("market-positive", odds=1.8, market_prob=0.7),
        validated("zero", odds=2, predicted_hit_prob=0.5,
                  probability_interval=[0.5, 0.6], probability_lower_bound=0.5),
        validated("tiny-positive", odds=2, predicted_hit_prob=0.5 + 1e-13,
                  probability_interval=[0.5 + 1e-13, 0.6], probability_lower_bound=0.5 + 1e-13),
    ]
    result = decisions(base + extras)
    assert [row["reason_code"] for row in result[3:]] == [
        "validated_extra", "rank", "rank", "rank", "validated_extra",
    ]
    assert sum(row["recommended"] for row in result) == 5


def test_ranking_has_no_high_odds_quota_and_does_not_round():
    rows = [pick("highest-odds", odds=2.1, market_prob=0.5)]
    rows += [pick(str(i), odds=1.5, market_prob=0.72 + i * 1e-10) for i in range(3)]
    result = decisions(rows)
    assert [row["league_rank"] for row in result] == [4, 3, 2, 1]
    assert not result[0]["recommended"]


def test_rank_compares_conservative_return_then_expected_return_then_probability():
    rows = [validated("point-low", predicted_hit_prob=0.68),
            validated("point-high", predicted_hit_prob=0.72),
            validated("lower-bound-high", predicted_hit_prob=0.7,
                      probability_interval=[0.67, 0.75], probability_lower_bound=0.67)]
    assert [row["league_rank"] for row in decisions(rows)] == [3, 2, 1]
    # Exactly equal return products; point probability precedes kickoff/key.
    assert [row["league_rank"] for row in decisions([
        pick("1", odds=2, market_prob=0.5), pick("2", odds=1.6, market_prob=0.625),
    ])] == [2, 1]


def test_ties_use_trimmed_kickoff_then_full_key_with_js_unicode_lexical_order():
    rows = [pick(key) for key in ["가", "a", "Z", "10", "2", "😀", "\ue000"]]
    result = decisions(rows)
    ranked = sorted(zip(rows, result), key=lambda item: item[1]["league_rank"])
    assert [row["game_no"] for row, _ in ranked] == ["10", "2", "Z", "a", "가", "😀", "\ue000"]
    rows = [pick("same", market_label="b"), pick("same", market_label=None, label="a")]
    assert [row["league_rank"] for row in decisions(rows)] == [2, 1]


def test_copies_input_order_and_full_list_ranking_include_started_rows():
    rows = [pick(str(i)) for i in range(4, 0, -1)]
    rows[0].update(recommendation_state="started_locked",
                   daily_recommendation={"recommended": True, "policy_version": "old"})
    before = deepcopy(rows)
    output = annotate_daily_values(rows)
    assert rows == before
    assert [row["game_no"] for row in output] == ["4", "3", "2", "1"]
    assert all(a is not b for a, b in zip(rows, output))
    assert output[0]["recommendation_state"] == "started_locked"
    assert output[0]["daily_recommendation"]["recommended"] is False
    assert annotate_daily_values(output) == output
    assert annotate_daily_values([]) == []


def test_build_annotates_after_retention_and_main_persists_same_artifact(monkeypatch):
    import today_combo

    current = [pick("current", recommendation_priority="primary")]
    retained = current + [pick("locked", recommendation_state="started_locked")]
    calls = []
    monkeypatch.setattr(today_combo, "_candidate_source", lambda: {
        "candidate_source": "live_odds", "generated_at": "2026-09-05T01:00:00Z", "year": 2026,
    })
    monkeypatch.setattr(today_combo, "legs_today", lambda **kwargs: current)
    monkeypatch.setattr(today_combo, "_enrich_candidates", lambda rows: rows)
    monkeypatch.setattr(today_combo, "select_event_candidates", lambda rows: rows)
    monkeypatch.setattr(today_combo, "live_snapshot", lambda *args: {})
    monkeypatch.setattr(today_combo, "load_artifact", lambda *args: None)
    monkeypatch.setattr(today_combo, "load_runtime_artifact",
                        lambda name, path: {"odds_bins": []} if name == "loss_grades" else {})

    def retain(rows, previous, now):
        assert rows is current
        calls.append("retain")
        return retained

    def annotate(rows):
        assert rows is retained
        assert calls == ["retain"]
        calls.append("annotate")
        return annotate_daily_values(rows)

    monkeypatch.setattr(today_combo, "retain_started_candidates", retain)
    monkeypatch.setattr(today_combo, "annotate_daily_values", annotate)
    payload = today_combo.build()
    assert calls == ["retain", "annotate"]
    assert payload["candidates"] == annotate_daily_values(retained)
    assert payload["n_candidates"] == 2
    assert payload["daily_recommendation_policy"]["policy_version"] == POLICY_VERSION
    assert payload["daily_recommendation_policy"]["kind"] == "heuristic_risk_limit"
    assert "경기별 방향" in payload["selection_policy"]
    assert "일일 하이라이트" in payload["selection_policy"]
    monkeypatch.setattr(today_combo, "build", lambda: payload)
    persisted = []
    monkeypatch.setattr(today_combo, "persist_artifact", lambda *args: persisted.append(args))
    assert today_combo.main() == 0
    assert persisted == [("today_combo", payload, today_combo.OUT)]


@pytest.mark.parametrize("browser_timezone", ["Asia/Seoul", "UTC", "America/Los_Angeles"])
def test_javascript_parity_when_frontend_is_available(browser_timezone):
    """Runs after cherry-pick, or against main's helper via DAILY_VALUE_JS_PATH."""
    module = Path(os.environ.get("DAILY_VALUE_JS_PATH", ROOT / "web/src/lib/daily-value.js"))
    node = shutil.which("node")
    if not module.is_file() or not node:
        pytest.skip("frontend helper/Node not available in isolated backend worktree")
    fixtures = [None, pick(), validated()]
    for field in ["odds", "market_prob", "predicted_hit_prob", "probability_lower_bound"]:
        fixtures.extend(validated(**{field: value}) for value in [
            None, "", "bad", True, False, 0, 1, "0.61", "NaN", "Infinity", 0.5,
            [], [1.7], [0.61], {}, "0_5", "0x1",
        ])
    for interval in [None, [], [0.65], [0.65, 0.75, 0.8], [0.65, 1],
                     [0.72, 0.75], [0.65, 0.69], [0.65, True], [0.65, 0.75],
                     [[0.65], 0.75], [{}, 0.75]]:
        fixtures.append(validated(probability_interval=interval))
    fixtures += [pick(odds=None, 배당=1.6, market_prob=None, 시장확률=0.6),
                 validated(probability_lower_bound=.5833, probability_interval=[.583333, .75]),
                 pick(market_prob=0.5, odds=1.7 - 1e-12),
                 pick(market_prob=0.5 - 1e-13), pick(market="홀짝"),
                 pick(final_reversal=True), pick(최종전환=True), pick(is_market_favorite=False)]
    groups = [fixtures, [pick(str(i)) for i in range(8)],
              [validated(str(i)) for i in range(8)],
              [pick("low", odds=1.4, market_prob=0.7), pick("primary")],
              [pick(key) for key in ["가", "a", "Z", "10", "2", "😀", "\ue000"]]]
    groups += [[pick(str(i), kickoff_at=stamp, date="09.05", **year)
                for stamp, year in [("2026-09-05T15:00:00Z", {}),
                                    ("2026-09-06T00:00:00+09:00", {}),
                                    (None, {}), (None, {"year": 2026})]
                for i in range(4)]]
    groups += [[pick(str(i), kickoff_at=stamp, date="09.05(토) 18:00")
                for i, stamp in enumerate([None, None, None, "2026-09-05T09:00:00Z"])],
               [pick(str(i), kickoff_at=stamp, date="09.05(토) 00:30")
                for i, stamp in enumerate(["2026-09-05T00:30:00", "2026-09-04T15:30:00Z",
                                         "2026-09-05T00:30:00+09:00", None])]]
    script = """
        import { pathToFileURL } from 'node:url';
        import { readFileSync } from 'node:fs';
        const {dailyValueMetrics, dailyValueDecisions} = await import(pathToFileURL(process.argv[1]));
        const {fixtures, groups} = JSON.parse(readFileSync(0, 'utf8'));
        const results = {metrics: fixtures.map(dailyValueMetrics),
          decisions: groups.map(rows => dailyValueDecisions(rows).map(({selection, ...rest}) => rest))};
        process.stdout.write(JSON.stringify(results));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(module)],
        input=json.dumps({"fixtures": fixtures, "groups": groups}, allow_nan=False),
        text=True, encoding="utf-8", capture_output=True, check=True,
        env={**os.environ, "TZ": browser_timezone},
    )
    actual = json.loads(result.stdout)
    assert actual["metrics"] == [daily_value_metrics(row) for row in fixtures]
    assert actual["decisions"] == [decisions(rows) for rows in groups]
