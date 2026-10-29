"""Offline regressions: JSONL only, no DB, requests, clock or trained model."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tracemalloc

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import operating_model_capture as comparison
from ai_decision import build_decision_snapshot
from prediction_ledger import LedgerCorruptionError, PredictionLedger
from prediction_runtime import PredictionRuntime, ledger_features, prediction_payload
from tests.test_fixed_tipster_hybrid import AT, decide, game, vote


@pytest.fixture(autouse=True)
def jsonl_only(monkeypatch):
    monkeypatch.delenv("PROODD_DB_PATH", raising=False)


def inputs(rows=None, g=None):
    g, snapshot = decide(g=g, rows=rows)
    g["decision_snapshot"] = snapshot
    return g, snapshot, ledger_features(g)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def test_operating_selection_and_probability_are_verbatim_and_controls_share_offers():
    g, s, f = inputs()
    p = f["model_comparison"]
    assert p["capture_kind"] == "prospective_same_snapshot"
    assert p["record_version"] == comparison.VERSION
    assert not p["affects_probability"] and not p["affects_selection"]
    arms = p["arms"]
    operating = arms["operating"]
    assert operating["selection_id"] == s["selection_id"]
    assert operating["offer_id"] == s["offer_id"]
    assert operating["probability"] == s["probability"]["final"] == .48
    assert operating["probability_basis"] == "market_reference_not_vote_share"
    assert operating["meta"]["selection_policy"] == "tipster-vote-v1"
    assert operating["meta"]["operating_version"] == s["model"]["operating_version"]
    assert arms["fixed91_same_electorate"]["probability"] == .9 * .48 + .1
    assert arms["market_reference"]["selection"] == "홈"
    assert arms["market_reference"]["probability"] == .52
    assert arms["home_always"]["probability"] is None
    assert arms["home_always"]["probability_basis"] == "not_applicable"
    for arm in arms.values():
        if arm["status"] == "available":
            row = next(r for r in f["options"] if r["selection_id"] == arm["selection_id"])
            assert all(arm[k] == row[k] for k in ("offer_id", "market", "label", "odds"))
    assert arms["saved_score_reference"]["reason"] == "missing_score_model_probability"
    assert arms["saved_score_reference"]["meta"]["promoted"] is False
    assert "options" not in p and '"evidence":' not in canonical(p)
    assert len(canonical(p).encode()) < 8192


@pytest.mark.parametrize("batch", [False, True])
def test_real_tipster_build_runtime_append_and_rehash_without_changing_decision(tmp_path, batch):
    g, s, f = inputs()
    baseline = deepcopy(g)
    prediction = prediction_payload(g)
    path = tmp_path / "predictions.jsonl"
    now = datetime(2026, 10, 25, 22, 1, tzinfo=timezone.utc)
    runtime = PredictionRuntime(path, clock=lambda: now)
    entry = dict(game=g, kickoff="2026-10-25T23:10:00Z", market_observed_at=AT)
    first = runtime.record_pregame_batch([entry])[0] if batch else runtime.record_pregame(**entry)
    assert first.appended
    record = first.record
    assert record["predictions"] == prediction
    assert record["features"]["model_comparison"] == f["model_comparison"]
    assert record["decision_id"] == s["decision_id"]
    assert g == baseline
    unsigned = {k: v for k, v in record.items() if k != "record_hash"}
    assert sha(unsigned) == record["record_hash"]
    assert PredictionLedger(path).records() == [record]
    before = path.read_bytes()
    retry = runtime.record_pregame_batch([entry])[0] if batch else runtime.record_pregame(**entry)
    assert retry is None and path.read_bytes() == before
    # A fresh observation really changes inputs and must get a second revision.
    changed = deepcopy(g)
    changed["options"][0]["배당"] = 1.51
    changed["decision_snapshot"] = build_decision_snapshot(changed, as_of=AT, tipster_votes=[vote()])
    second = runtime.record_pregame(**dict(entry, game=changed))
    assert second.appended
    assert second.record["previous_record_hash"] == record["record_hash"]
    assert len(PredictionLedger(path).records()) == 2
    tampered = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    tampered["features"]["model_comparison"]["arms"]["home_always"]["probability"] = .99
    path.write_text(canonical(tampered) + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorruptionError):
        PredictionLedger(path).records()


def test_no_backfill_or_clock_only_revision(tmp_path):
    g, s, f = inputs()
    path = tmp_path / "old.jsonl"
    now = lambda: datetime(2026, 10, 25, 22, 1, tzinfo=timezone.utc)
    old_features = {k: v for k, v in f.items() if k != "model_comparison"}
    ledger = PredictionLedger(path, clock=now)
    ledger.append_prediction(g, s, features=old_features, predictions=prediction_payload(g),
                             kickoff="2026-10-25T23:10:00Z", market_observed_at=AT)
    before = path.read_bytes()
    runtime = PredictionRuntime(path, clock=now)
    assert runtime.record_pregame(g, kickoff="2026-10-25T23:10:00Z", market_observed_at=AT) is None
    later = deepcopy(g)
    later["decision_snapshot"] = build_decision_snapshot(later, as_of="2026-10-25T22:05:00Z", tipster_votes=[vote()])
    assert runtime.record_pregame(later, kickoff="2026-10-25T23:10:00Z", market_observed_at=AT) is None
    assert path.read_bytes() == before


def test_no_votes_abstains_even_with_anchor_version():
    _, s, f = inputs(rows=[])
    p = f["model_comparison"]; arms = p["arms"]
    assert s["action"] == "withhold"
    assert arms["operating"]["status"] == "abstain"
    assert arms["operating"]["meta"]["selection_policy"] == "tipster-vote-v1"
    assert arms["operating"]["meta"]["operating_version"] == "shin-market-anchor-v1"
    assert arms["fixed91_same_electorate"]["reason"] == "no_valid_votes"
    assert arms["market_reference"]["status"] == "available"
    assert arms["home_always"]["status"] == "available"


def test_tied_votes_still_allow_frozen_comparison_without_changing_abstention():
    rows = [vote(), vote(slug="second", pick_id="home", selection="NYY ML -110", american_odds=-110)]
    _, s, f = inputs(rows=rows)
    arms = f["model_comparison"]["arms"]
    assert s["action"] == "withhold" and arms["operating"]["status"] == "abstain"
    assert arms["fixed91_same_electorate"]["status"] == "available"
    assert arms["fixed91_same_electorate"]["probability"] == .9 * .52 + .1 * .5


def test_three_way_argmax_draw_and_frozen_same_electorate():
    g = game(); g.update(sport="sc", league="EPL")
    g["options"] = [dict(market="승무패", label="", 선택=side, 배당=3., 시장확률=p,
                         모델확률=q) for side, p, q in [("홈", .30, .3333), ("무", .40, .3333), ("원정", .30, .3333)]]
    from public_tipster_votes import SOURCE, fixture_key
    row = dict(source=SOURCE, event_type="first_observed", eligible_pre_event=True,
               provenance="live_http_observation", market_type="full_game_winner", fixture=fixture_key(g),
               side="무", voter="publisher", pick_id="draw", source_url="https://pick79.com/posts/detail/test/",
               observed_at=AT, content_hash="draw-hash")
    _, s, f = inputs(rows=[row], g=g)
    arms = f["model_comparison"]["arms"]
    assert f["model_comparison"]["roster_size"] == 3
    assert arms["operating"]["probability"] == s["probability"]["final"] == .4
    assert arms["market_reference"]["selection"] == "무"
    assert arms["fixed91_same_electorate"]["probability"] == .9 * .4 + .1
    assert arms["home_always"]["selection"] == "홈"
    assert arms["saved_score_reference"]["status"] == "available"


def test_ties_use_selection_id_order_independent_of_original_option_order():
    _, s, f = inputs()
    f["tipster_hybrid"]["vote_shares"] = {"홈": .5, "원정": .5, "무": 0}
    for r in f["options"][:2]:
        r["market_probability"] = r["score_model_probability"] = .5
    p = comparison.capture(s, f)
    expected = min(r["selection_id"] for r in f["options"][:2])
    for name in ("market_reference", "fixed91_same_electorate", "saved_score_reference"):
        assert p["arms"][name]["selection_id"] == expected
    f["options"].reverse()
    assert comparison.capture(s, f) == p


@pytest.mark.parametrize("change,reason", [
    ("duplicate_selection", "duplicate_selection_id"),
    ("duplicate_offer", "duplicate_offer_id"),
    ("extra_offer", "duplicate_side_or_offer"),
    ("missing_side", "incomplete_full_game_roster"),
    ("mixed_market", "missing_or_multiple_full_game_markets"),
    ("missing_id", "missing_selection_or_offer_id"),
    ("line", "non_full_game_line"),
    ("bad_odds", "invalid_odds"),
])
def test_invalid_roster_fails_closed(change, reason):
    _, s, f = inputs()
    a, b = f["options"][:2]
    if change == "duplicate_selection": b["selection_id"] = a["selection_id"]
    elif change == "duplicate_offer": b["offer_id"] = a["offer_id"]
    elif change == "extra_offer":
        f["options"].append(dict(a, selection_id="another", offer_id="another-offer"))
    elif change == "missing_side": f["options"].remove(b)
    elif change == "mixed_market": b["market"] = "승무패"
    elif change == "missing_id": b["offer_id"] = None
    elif change == "line": b["line"] = 1
    elif change == "bad_odds": b["odds"] = 1
    p = comparison.capture(s, f)
    assert p["status"] == "unavailable" and p["reason"] == reason
    assert all(a["status"] == "unavailable" for a in p["arms"].values())


@pytest.mark.parametrize("value,reason", [(None, "missing_market_probability"),
    (-.1, "invalid_market_probability"), (True, "invalid_market_probability"),
    (.8, "invalid_market_probability_sum")])
def test_bad_market_vector_does_not_manufacture_fixed91(value, reason):
    _, s, f = inputs(); f["options"][0]["market_probability"] = value
    arms = comparison.capture(s, f)["arms"]
    assert arms["market_reference"]["reason"] == reason
    assert arms["fixed91_same_electorate"]["reason"] == reason
    assert arms["home_always"]["probability"] is None


@pytest.mark.parametrize("shares", [{"홈": .2, "원정": .2}, {"홈": None, "원정": 1},
    {"홈": -1, "원정": 2}, {"홈": False, "원정": 1}, {"홈": 0, "원정": 1, "무": .1},
    {"홈": 0, "원정": 1, "other": 0}])
def test_invalid_votes_never_fallback_or_infer(shares):
    _, s, f = inputs(); f["tipster_hybrid"]["vote_shares"] = shares
    assert comparison.capture(s, f)["arms"]["fixed91_same_electorate"]["reason"] == "invalid_vote_vector"


def test_score_reference_requires_complete_valid_vector_and_never_claims_promotion():
    _, s, f = inputs()
    for row, p in zip(f["options"], (.3, .7)):
        row["score_model_probability"] = p
    p = comparison.capture(s, f)
    score = p["arms"]["saved_score_reference"]
    assert score["status"] == "available" and score["selection"] == "원정"
    assert score["meta"] == dict(source=None, artifact_identity="unverified", training_evidence="unverified", promoted=False)
    f["options"][0]["score_model_probability"] = .8
    assert comparison.capture(s, f)["arms"]["saved_score_reference"]["reason"] == "invalid_score_model_probability_sum"
    f["options"][0]["score_model_probability"] = None
    assert comparison.capture(s, f)["arms"]["saved_score_reference"]["reason"] == "missing_score_model_probability"


def test_nullable_operating_probability_and_wrong_offer_are_unavailable():
    _, s, f = inputs(); s["probability"]["final"] = None
    p = comparison.capture(s, f)
    assert p["arms"]["operating"]["probability"] is None
    assert p["arms"]["operating"]["reason"] == "missing_or_invalid_operating_probability"
    s["probability"]["final"] = .48; s["offer_id"] = "wrong-offer"
    assert comparison.capture(s, f)["arms"]["operating"]["reason"] == "operating_selection_outside_shared_roster"
    s["selection_id"] = None
    assert comparison.capture(s, f)["arms"]["operating"]["status"] == "abstain"


@pytest.mark.parametrize("field,value,reason", [
    ("as_of", "invalid", "invalid_prediction_as_of"),
    ("as_of", "2026-10-25T22:00:00", "invalid_prediction_as_of"),
    ("built_at", "invalid", "invalid_source_generated_at"),
    ("built_at", "2026-10-25T22:00:00", "invalid_source_generated_at"),
])
def test_malformed_times_are_not_guessed(field, value, reason):
    _, s, f = inputs()
    if field == "as_of": s[field] = value
    else: s["audit"][field] = value
    p = comparison.capture(s, f)
    assert p["status"] == "unavailable" and p["reason"] == reason
    assert "captured_at" not in p and "market_observed_at" not in p


@pytest.mark.parametrize("value,reason", [("invalid", "invalid_vote_observed_at"),
    (None, "invalid_vote_observed_at"), ("2026-10-25T22:01:00Z", "vote_observed_after_prediction_as_of")])
def test_bad_vote_times_only_disable_fixed_control(value, reason):
    _, s, f = inputs(); f["tipster_hybrid"]["latest_observed_at"] = value
    arms = comparison.capture(s, f)["arms"]
    assert arms["fixed91_same_electorate"]["reason"] == reason
    assert arms["operating"]["status"] == "available"


def test_after_kickoff_source_time_is_preserved_for_outer_auditor_and_reconstruction_rejected():
    _, s, f = inputs()
    s["audit"]["built_at"] = "2026-10-26T05:00:00Z"
    p = comparison.capture(s, f)
    assert p["source_generated_at"] == s["audit"]["built_at"]
    assert p["eligibility"] == "requires_outer_ledger_timing_audit"
    assert "captured_at" not in p and "kickoff" not in p
    s["audit"]["reconstructed_at"] = s["audit"]["built_at"]
    assert comparison.capture(s, f)["reason"] == "retrospective_snapshot_unsupported"


def test_evaluator_replay_is_explicitly_retrospective_with_identical_arm_computation():
    _, s, f = inputs(rows=[])
    fresh = comparison.capture(s, f)
    replay = comparison.capture(s, f, capture_kind="retrospective_same_snapshot_control")
    assert replay["capture_kind"] == "retrospective_same_snapshot_control"
    assert replay["arms"] == fresh["arms"]
    assert replay["input_batch_hash"] == fresh["input_batch_hash"]
    s["audit"]["reconstructed_at"] = "2026-10-29T10:00:00Z"
    assert comparison.capture(s, f)["reason"] == "retrospective_snapshot_unsupported"
    replay = comparison.capture(s, f, capture_kind="retrospective_same_snapshot_control")
    assert replay["status"] == "captured"
    assert replay["arms"]["operating"]["status"] == "abstain"
    assert "captured_at" not in replay
    assert comparison.capture(s, f, capture_kind="typo")["reason"] == "unsupported_capture_kind"


def test_repeat_snapshot_detaches_inputs_and_each_result_and_reproduces_recipe_hash():
    g, s, f = inputs(); baseline = deepcopy((g, s, f))
    p = comparison.capture(s, f)
    assert comparison.capture(s, f) == p
    assert (g, s, f) == baseline
    for name, arm in p["arms"].items():
        frozen = comparison.recipe(name)
        assert arm["recipe_hash"] == sha(frozen)
        frozen["rule"] = "changed"
        assert comparison.recipe(name)["rule"] != "changed"
    f["options"][0]["odds"] = 7
    s["model"]["operating_version"] = "mutated"
    assert p["arms"]["home_always"]["odds"] == 1.49
    assert p["arms"]["operating"]["meta"]["operating_version"] == "tipster-vote-v1"
    p["arms"]["home_always"]["odds"] = 99
    assert comparison.capture(baseline[1], baseline[2])["arms"]["home_always"]["odds"] == 1.49


def test_shared_hashes_bind_price_offer_vote_and_score_without_duplicate_rosters():
    _, s, f = inputs(); p = comparison.capture(s, f)
    for field, value in (("odds", 1.6), ("offer_id", "other-offer"), ("score_model_probability", .4)):
        changed = deepcopy(f); changed["options"][0][field] = value
        other = comparison.capture(s, changed)
        assert other["input_batch_hash"] != p["input_batch_hash"]
        assert (other["price_roster_hash"] != p["price_roster_hash"]) == (field != "score_model_probability")
    changed = deepcopy(f); changed["tipster_hybrid"]["evidence_hash"] = "another-electorate"
    assert comparison.capture(s, changed)["input_batch_hash"] != p["input_batch_hash"]


def test_unexpected_failure_and_nonfinite_inputs_fail_closed(monkeypatch):
    _, s, f = inputs()
    f["options"][0]["market_probability"] = float("nan")
    p = comparison.capture(s, f)
    assert p["arms"]["market_reference"]["reason"] == "invalid_market_probability"
    assert p["arms"]["fixed91_same_electorate"]["reason"] == "invalid_market_probability"
    canonical(p)
    monkeypatch.setattr(comparison, "_capture", lambda *args: (_ for _ in ()).throw(RuntimeError("secret")))
    assert comparison.capture(s, f)["reason"] == "unsupported_input"
    assert "secret" not in canonical(comparison.capture(s, f))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.1, True])
def test_nonfinite_or_invalid_score_only_disables_score_reference(value):
    _, s, f = inputs()
    f["options"][0]["score_model_probability"] = value
    f["options"][1]["score_model_probability"] = .5
    p = comparison.capture(s, f)
    assert p["arms"]["saved_score_reference"]["reason"] == "invalid_score_model_probability"
    assert p["arms"]["operating"]["status"] == "available"
    assert p["arms"]["market_reference"]["status"] == "available"


def test_withheld_invalid_vote_source_cannot_become_a_valid_electorate():
    _, s, f = inputs()
    f["tipster_hybrid"].update(status="withheld", reason="invalid_full_game_moneyline")
    p = comparison.capture(s, f)
    assert p["arms"]["fixed91_same_electorate"]["reason"] == "no_valid_votes"


def test_capture_memory_and_storage_bound_with_many_irrelevant_options():
    _, s, f = inputs()
    # Share large irrelevant inputs rather than create 10,000 copies; capture
    # must not copy/hash/store that context or rescan full options per arm.
    irrelevant = dict(market="핸디캡", label="H -1.5", note="x" * 100000)
    f["options"].extend([irrelevant] * 10000)
    f["player_context"] = {"huge_unused_context": "x" * 1000000}
    class Once(list):
        passes = 0
        def __iter__(self):
            self.passes += 1
            assert self.passes == 1
            return super().__iter__()
    f["options"] = Once(f["options"])
    tracemalloc.start()
    try:
        p = comparison.capture(s, f)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert p["status"] == "captured"
    assert f["options"].passes == 1
    assert len(canonical(p).encode()) < 8192
    assert peak < 256000


def test_oversized_metadata_and_absent_snapshot_do_not_fail_runtime():
    g, s, f = inputs(); s["model"]["operating_version"] = "x" * 9000
    assert comparison.capture(s, f)["reason"] == "invalid_or_oversized_metadata"
    assert comparison.capture(None, f)["reason"] == "invalid_snapshot_or_features"
    del g["decision_snapshot"]
    assert ledger_features(g)["model_comparison"]["status"] == "unavailable"
