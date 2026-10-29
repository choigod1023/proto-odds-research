"""Portable full archived parity and strict current-source input tests, offline."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import mlb_frozen_candidate as bridge

DATA = ROOT / "data/model_candidates/mlb"


@pytest.fixture(scope="module")
def archived():
    raw = (DATA / "parity-fixtures.jsonl.gz").read_bytes()
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf8"))
    assert hashlib.sha256(raw).hexdigest() == manifest["fixture_sha256"]
    return [json.loads(line) for line in gzip.decompress(raw).decode().splitlines()]


@pytest.fixture(scope="module")
def bundles():
    return {season: bridge.load_mlb_artifacts(season) for season in (2025, 2026)}


def run(fixture, bundles):
    season = int(fixture["snapshot"]["start_at"][:4])
    model, priors = bundles[season]
    return bridge.infer_mlb_candidate(fixture["snapshot"], decision_at=fixture["decision_at"],
        matchup=fixture["matchup"], odds=fixture["odds"], model=model, prior_tables=priors)


def settlement(selection, prices, hg, ag):
    # Separate from inference; labels enter ONLY after choices are finalized.
    if hg == ag:
        return dict(pick=selection, correct=0, push=1, net_krw=0)
    correct = (hg > ag) == (selection == "home")
    net = int((Decimal(10000) * (Decimal(str(prices[selection])) - 1)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)) if correct else -10000
    return dict(pick=selection, correct=int(correct), push=0, net_krw=net)


def test_all_310_saved_predictions_features_decisions_and_money(archived, bundles):
    assert Counter(f["labels"]["cohort"] for f in archived) == {"old116": 116, "external230": 194}
    stages = Counter()
    for f in archived:
        result = run(f, bundles)
        assert result["status"] == "available", result["reasons"]
        assert result["probability_home"] == pytest.approx(f["expected"]["probability_home"], abs=1e-12, rel=0)
        assert result["features"] == pytest.approx(f["expected"]["features"], abs=1e-12, rel=0)
        assert result["model_stage"] == f["expected"]["model_stage"]
        assert result["result_used"] is False
        assert result["operating_model_reproduced"] is False
        assert result["time_evidence"] == "retrospective_timecode_not_live_capture"
        assert result["odds_time_verified"] is False
        # Distinguish missing legacy feed start evidence, never manufacture it.
        assert result["feed_start_verified"] == ("feed_start_at" in f["snapshot"])
        stages[result["model_stage"]] += 1
        assert settlement(result["selection"], f["odds"], f["labels"]["hg"], f["labels"]["ag"]) == f["labels"]["ledger"]["primary"]
    assert stages["before2025"] > 0 and stages["before2026"] > 0


@pytest.fixture
def live(archived):
    # Controlled source-contract test, NOT a claim of historical live collection.
    f = deepcopy(next(f for f in archived if f["snapshot"]["start_at"].startswith("2026")))
    snapshot = f["snapshot"]
    start = bridge._time(snapshot["start_at"])
    snapshot.update(source_kind="official_live_completed_http", version="mlb-candidate-feed-v1",
                    status="captured", source_status="Warmup", feed_start_at=start.isoformat(),
                    starter_identity_kind="official_published_probable_pitcher", starter_final_confirmed=False,
                    lineup_identity_kind="official_boxscore_batting_order",
                    snapshot_at=(start - timedelta(minutes=60)).isoformat(),
                    observed_at=(start - timedelta(minutes=50)).isoformat(),
                    cutoff_at=(start - timedelta(minutes=50)).isoformat(), source_url=snapshot["url"])
    for key in ("collected_at", "url", "home", "away", "accepted"):
        snapshot.pop(key, None)
    f["decision_at"] = (start - timedelta(minutes=45)).isoformat()
    f["odds"]["observed_at"] = (start - timedelta(minutes=46)).isoformat()
    f["odds"].pop("time_scope")
    return f


def test_actual_parent_collector_shape_supported_without_substitutes(live, bundles):
    before = deepcopy(live)
    r = run(live, bundles)
    assert r["status"] == "available", r["reasons"]
    assert r["time_evidence"] == "contemporaneous_live_T30"
    assert r["odds_time_verified"] is True
    assert r["evidence"]["collected_at"] == live["snapshot"]["observed_at"]
    assert r["evidence"]["pregame_gate"] == "collector_v1_S_P_no_pitch_nine_battingOrder"
    assert r["starter_identity_kind"] == "official_published_probable_pitcher"
    assert r["starter_final_confirmed"] is False
    assert r["probability_home"] == pytest.approx(live["expected"]["probability_home"], abs=1e-12, rel=0)
    assert live == before


@pytest.mark.parametrize("change,reason", [
    (lambda f: f["snapshot"]["sides"]["home"]["lineup"].pop(), "home:nine_announced_starters_required"),
    (lambda f: f["snapshot"]["sides"]["away"]["lineup"].__setitem__(0, f["snapshot"]["sides"]["away"]["lineup"][1]), "away:invalid_or_duplicate_lineup_ids"),
    (lambda f: f["snapshot"]["sides"]["home"].pop("starter"), "home:starting_pitcher_required"),
    (lambda f: f["snapshot"].__setitem__("source_status", "In Progress"), "status:not_pregame"),
    (lambda f: f["snapshot"].__setitem__("status", "unavailable"), "collector:validated_capture_required"),
    (lambda f: f["snapshot"].__setitem__("version", "unknown"), "collector:validated_capture_required"),
    (lambda f: f.__setitem__("decision_at", f["snapshot"]["start_at"]), "snapshot_at:not_pregame_at_decision"),
    (lambda f: f.__setitem__("decision_at", (bridge._time(f["snapshot"]["start_at"])-timedelta(minutes=29)).isoformat()), "decision_at:after_live_T30"),
    (lambda f: f["snapshot"].__setitem__("observed_at", f["snapshot"]["start_at"]), "collected_at:after_decision_or_before_snapshot"),
    (lambda f: f["snapshot"].__setitem__("snapshot_at", "2026-08-01T12:00:00"), "snapshot_at:aware_time_required"),
    (lambda f: f["snapshot"].__setitem__("feed_start_at", f["decision_at"]), "feed_start_at:matchup_start_mismatch"),
    (lambda f: f["snapshot"].__setitem__("gamePk", 42), "matchup:gamePk_mismatch"),
    (lambda f: f["matchup"].__setitem__("league", "KBO"), "unsupported_league"),
    (lambda f: f["snapshot"].__setitem__("home", "wrong club"), "matchup:home_mismatch"),
    (lambda f: f["snapshot"].__setitem__("response_sha256", "unhashed"), "source:response_sha256_required"),
    (lambda f: f["snapshot"].__setitem__("source_url", "https://example.com/feed"), "source:official_url_required"),
    (lambda f: f["odds"].__setitem__("home", float("nan")), "odds:home:invalid_decimal_price"),
    (lambda f: f["odds"].__setitem__("away", 1), "odds:away:invalid_decimal_price"),
    (lambda f: f["odds"].__setitem__("observed_at", None), "odds:observed_at_missing_or_after_decision"),
    (lambda f: f["odds"].__setitem__("observed_at", f["snapshot"]["start_at"]), "odds:observed_at_missing_or_after_decision"),
    (lambda f: f["odds"].pop("snapshot_id"), "odds:provenance_required"),
])
def test_fail_closed(live, bundles, change, reason):
    change(live)
    r = run(live, bundles)
    assert r["status"] == "unavailable"
    assert r["reasons"] == [reason]
    assert r["selection"] is None and r["probability_home"] is None


@pytest.mark.parametrize("field", bridge.BAT_FIELDS)
def test_each_required_batting_count_missing_is_explicit(live, bundles, field):
    player = live["snapshot"]["sides"]["home"]["lineup"][0]
    player["batting"].pop(field)
    assert run(live, bundles)["reasons"] == [f"home:{player['id']}:batting:{field}:missing_or_invalid"]


@pytest.mark.parametrize("field", bridge.PIT_FIELDS)
def test_each_required_pitching_count_missing_is_explicit(live, bundles, field):
    player = live["snapshot"]["sides"]["away"]["starter"]
    player["pitching"].pop(field)
    assert run(live, bundles)["reasons"] == [f"away:{player['id']}:pitching:{field}:missing_or_invalid"]


def test_labels_do_not_affect_output_or_hash(live, bundles):
    original = run(live, bundles)
    live["labels"] = {"hg": 999, "ag": -1}
    live["snapshot"].update(home_score=999, away_score=-1, target={"winner": "away"})
    live["snapshot"]["sides"]["home"]["lineup"][0]["batting"]["outcome"] = "away"
    assert run(live, bundles) == original


def test_input_order_stats_odds_and_times_are_bound_in_hash(live, bundles):
    original = run(live, bundles)
    live["snapshot"]["sides"]["home"]["lineup"].reverse()
    reordered = run(live, bundles)
    assert reordered["status"] == "available"
    assert reordered["input_hash"] != original["input_hash"]
    assert reordered["feature_hash"] != original["feature_hash"]
    live["odds"]["home"] = 3.0
    assert run(live, bundles)["input_hash"] != reordered["input_hash"]


def test_explicit_zero_exposure_and_missing_prior_are_original_eb(live, bundles):
    player = live["snapshot"]["sides"]["home"]["lineup"][0]
    player["id"] = 999999999
    player["batting"] = dict.fromkeys(bridge.BAT_FIELDS, 0)
    assert run(live, bundles)["status"] == "available"
    vector = bridge.player_features(player, "batter", bundles[2026][1]["payload"]["tables"]["batter"])
    assert vector[-3:] == [1., 1., 1.]


def test_artifact_tampering_and_rehashed_retraining_rejected(live, bundles):
    model, prior = deepcopy(bundles[2026])
    model["payload"]["models"]["before2026"]["beta"][0] += .1
    args = dict(snapshot=live["snapshot"], decision_at=live["decision_at"], matchup=live["matchup"], odds=live["odds"],
                model=model, prior_tables=prior)
    assert bridge.infer_mlb_candidate(**args)["reasons"] == ["model:artifact_hash_mismatch"]
    model["sha256"] = bridge.canonical_hash(model["payload"])
    assert bridge.infer_mlb_candidate(**args)["reasons"] == ["model:not_frozen_artifact"]
    args["model"] = bundles[2026][0]
    args["prior_tables"] = bundles[2025][1]
    assert bridge.infer_mlb_candidate(**args)["reasons"] == ["priors:wrong_season"]


def test_live_t30_boundary_and_no_filesystem_in_pure_inference(live, bundles, monkeypatch):
    live["decision_at"] = (bridge._time(live["snapshot"]["start_at"]) - timedelta(minutes=30)).isoformat()
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem or implicit loader used")
    monkeypatch.setattr(bridge, "load_mlb_artifacts", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    assert run(live, bundles)["status"] == "available"


def test_invalid_or_non_mlb_is_rejected_before_optional_local_loading(live, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ineligible fixture loaded artifacts")
    monkeypatch.setattr(bridge, "load_mlb_artifacts", forbidden)
    live["matchup"]["league"] = "KBO"
    result = bridge.infer_mlb_candidate_local(live["snapshot"], decision_at=live["decision_at"],
                                             matchup=live["matchup"], odds=live["odds"])
    assert result["reasons"] == ["unsupported_league"]


def test_local_loader_cache_is_bounded_and_cannot_be_mutated():
    model, prior = bridge.load_mlb_artifacts(2026)
    model["payload"]["models"].clear()
    prior["payload"]["tables"].clear()
    fresh_model, fresh_prior = bridge.load_mlb_artifacts(2026)
    assert fresh_model["payload"]["models"] and fresh_prior["payload"]["tables"]
    assert bridge._read_bundle.cache_info().maxsize == 2
    with pytest.raises(bridge.Unavailable, match="unsupported_season"):
        bridge.load_mlb_artifacts(2027)


def test_standard_library_only_import_no_training_numpy_scipy_or_implicit_load():
    code = (f"import sys;sys.path.insert(0,{str(ROOT / 'src')!r});import mlb_frozen_candidate as m;"
            "assert 'numpy' not in sys.modules and 'scipy' not in sys.modules;"
            "assert not any(k.startswith('mlb_lineup_nonlinear') for k in sys.modules);"
            "assert m._read_bundle.cache_info().currsize == 0")
    subprocess.run([sys.executable, "-I", "-c", code], check=True, capture_output=True, text=True)


def test_clipping_and_tie_semantics():
    n = len(bridge.FEATURE_NAMES)
    model = dict(center=[0.] * n, scale=[1.] * n, beta=[0.] * (n+1))
    assert bridge.predict_features([1e300] * n, model) == .5
    model["beta"][0] = 1e300
    assert bridge.predict_features([0.] * n, model) == 1/(1+__import__("math").exp(-40))
    model["beta"][0] = 0.
    model["beta"][1] = 1.
    assert bridge.predict_features([1e300] * n, model) == 1/(1+__import__("math").exp(-6))


@pytest.mark.parametrize("change,reason", [
    (lambda f: f["snapshot"].__setitem__("sides", None), "sides:required_mapping"),
    (lambda f: f["snapshot"]["sides"].__setitem__("away", None), "away:required_mapping"),
    (lambda f: f["snapshot"].__setitem__("starter_final_confirmed", True), "starter:unsupported_identity_claim"),
    (lambda f: f["snapshot"].__setitem__("lineup_identity_kind", "projected"), "lineup:unsupported_identity_claim"),
    (lambda f: f["matchup"].__setitem__("start_at", f["decision_at"]), "matchup:start_at_mismatch"),
    (lambda f: f["snapshot"]["sides"]["away"]["lineup"][0].__setitem__("id", f["snapshot"]["sides"]["home"]["lineup"][0]["id"]), "sides:opponents_share_player_identity"),
])
def test_structural_identity_and_confirmation_claims(live, bundles, change, reason):
    change(live)
    assert run(live, bundles)["reasons"] == [reason]


def test_feature_helper_cannot_silently_accept_eight_batters(live, bundles):
    live["snapshot"]["sides"]["home"]["lineup"].pop()
    with pytest.raises(bridge.Unavailable, match="nine_announced_starters_required"):
        bridge.feature_row(live["snapshot"], bundles[2026][1]["payload"]["tables"])


def test_retro_unverified_quote_time_cannot_be_carried_into_live_input(live, bundles):
    live["odds"].update(observed_at=None, time_scope="archived_unverified_time")
    assert run(live, bundles)["reasons"] == ["odds:observed_at_missing_or_after_decision"]


def test_no_historical_acceptance_no_inference(archived, bundles):
    f = deepcopy(archived[0])
    f["snapshot"].pop("accepted")
    assert run(f, bundles)["reasons"] == ["historical:archive_acceptance_required"]


def test_unsupported_future_season_never_loads_an_arbitrary_prior(live, monkeypatch):
    for key in ("start_at", "feed_start_at", "snapshot_at", "observed_at", "cutoff_at"):
        live["snapshot"][key] = live["snapshot"][key].replace("2026-", "2027-", 1)
    live["decision_at"] = live["decision_at"].replace("2026-", "2027-", 1)
    live["odds"]["observed_at"] = live["odds"]["observed_at"].replace("2026-", "2027-", 1)
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported season loaded artifacts")
    monkeypatch.setattr(bridge, "load_mlb_artifacts", forbidden)
    result = bridge.infer_mlb_candidate_local(live["snapshot"], decision_at=live["decision_at"],
                                             matchup=live["matchup"], odds=live["odds"])
    assert result["reasons"] == ["unsupported_season:only_2025_2026_frozen"]


def test_local_wrapper_matches_explicit_pure_api(live, bundles):
    result = bridge.infer_mlb_candidate_local(live["snapshot"], decision_at=live["decision_at"],
                                             matchup=live["matchup"], odds=live["odds"])
    assert result == run(live, bundles)
