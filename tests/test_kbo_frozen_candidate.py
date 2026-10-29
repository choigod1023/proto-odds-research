"""Portable frozen-archive replay and causal same-snapshot boundary regressions."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import random
import sys
import tracemalloc

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import kbo_frozen_candidate as kbo

DATA = ROOT / "data/model_candidates/kbo"


def read(name):
    with gzip.open(DATA / name, "rt", encoding="utf8") as stream:
        return [json.loads(line) for line in stream]


def settle(row, probability):
    if row["hg"] == row["ag"]:
        return 0, 0, 0
    side = "home" if probability >= .5 else "away"
    hit = (side == "home") == (row["hg"] > row["ag"])
    price = row["prices"][row["order"].index(side)]
    money = (Decimal(str(price)) - 1) * 10000 if hit else Decimal(-10000)
    assert money == int(money)
    return int(hit), int(money), 1


def metric(rows, arm):
    profits, wins, settled, daily = [], 0, 0, {}
    for row in rows:
        p = row[arm]["probability"]
        h, money, n = settle(row, p)
        profits.append(money)
        wins += h
        settled += n
        daily[row["date"]] = daily.get(row["date"], 0) + money
    balance = peak = drawdown = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
    return dict(n=len(rows), wins=wins, settled=settled, net_krw=sum(profits),
                roi=sum(profits) / (10000 * len(rows)), daily_close_max_drawdown_krw=drawdown)


def test_bundled_inputs_match_saved_hashes():
    manifest = json.loads((DATA / "input-manifest.json").read_text(encoding="utf8"))
    for name, item in manifest["bundled"].items():
        payload = (DATA / name).read_bytes()
        if not name.endswith(".gz"):
            payload = payload.replace(b"\r\n", b"\n")
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    assert kbo.artifact()["final_fit"]["training_years"] == ["2023", "2024"]
    assert kbo.artifact()["final_fit"]["beta"] == [-0.06637396467535583]
    assert kbo.artifact()["final_fit"]["scale"] == [0.28102288647783685]


@pytest.mark.parametrize("partition,expected_n,fit", [("evaluation", 198, "final_fit"), ("validation", 712, "selection_fit")])
def test_every_archived_probability_pick_and_integer_settlement(partition, expected_n, fit):
    rows = [r for r in read("archived-reproduction.jsonl.gz") if r["role"] == partition]
    assert len(rows) == expected_n
    for row in rows:
        result = kbo.infer_archived(row, source_signature=kbo.artifact()["archive_sources"], partition=partition)
        expected = row["expected"]
        assert result["status"] == "available", result
        assert result["fit"] == fit
        assert result["home_probability"] == pytest.approx(expected["probability"], abs=1e-12, rel=0)
        assert result["side"] == expected["side"]
        assert settle(row, result["home_probability"]) == (expected["hit"], expected["profit_krw"], expected["settled"])
        assert result["contemporary_availability_proven"] is False
        assert result["availability_basis"] == kbo.ARCHIVE_POLICY


def test_replay_all_archived_day_priors_features_and_end_seed_without_fitting():
    games = read("archived-games.jsonl.gz")
    snapshots = {r["date"]: r for r in read("archived-e0-snapshots.jsonl.gz")}
    features = read("archived-reproduction.jsonl.gz")
    by_day = {d: list(g) for d, g in groupby(features, key=lambda r: r["date"])}
    observed = {d: list(g) for d, g in groupby(games, key=lambda r: r["date"])}
    state, compared, feature_n = kbo._E0State(), 0, 0
    for day in sorted(set(snapshots) | set(observed)):
        state.advance(day)
        if day in snapshots:
            snapshot = snapshots[day]
            assert [state.mean[t] for t in kbo.TEAMS] == pytest.approx([r[0] for r in snapshot["mean"]], abs=1e-12, rel=0)
            assert [state.counts[t] for t in kbo.TEAMS] == snapshot["counts"]
            assert state.latest == snapshot["latest_observation"]
            assert state.total == snapshot["total_observations"]
            compared += 1
        for row in by_day.get(day, []):
            actual = state.observe(row["home"], row["away"])
            archived = row["dynamic"]["e0"]
            for name in ("x", "state_logit", "home_state", "away_state"):
                assert actual[name] == pytest.approx(archived[name], abs=1e-12, rel=0)
            assert actual["home_games"] == archived["home_games"]
            assert actual["away_games"] == archived["away_games"]
            feature_n += 1
        state.update(observed.get(day, []))
    seed = kbo.historical_seed()
    for team in kbo.TEAMS:
        assert state.mean[team] == pytest.approx(seed["mean"][team], abs=1e-12, rel=0)
    assert state.counts == seed["counts"]
    assert state.team_latest == seed["team_latest"]
    assert state.total == seed["total_observations"] == 2913
    assert feature_n == 910
    assert compared == 634


def target(day="2026-10-29", home="LG", away="KT"):
    return dict(league="KBO", date=day, home=home, away=away, kickoff=day + "T18:30:00+09:00")


def build_args(day="2026-10-29", results=None, seed=True):
    results = [] if results is None else deepcopy(results)
    params = dict(fixture=target(day), decision_at=day + "T17:00:00+09:00", results=results,
                  source_signature=dict(source_id="test-official-complete-export", coverage_verified=True,
                                        coverage_evidence_hash=kbo.signature({"synthetic_test_coverage": True}),
                                        coverage_available_at=day + "T08:00:00+09:00",
                                        records_hash=kbo.signature(sorted(results, key=lambda r: (r["date"], r["source_fixture_id"]))),
                                        coverage_from="2026-10-28" if seed else "2023-04-01",
                                        complete_through=(date.fromisoformat(day) - timedelta(days=1)).isoformat()))
    if seed:
        params["seed"] = kbo.historical_seed()
        params["source_signature"].update(seed_hash=kbo.SEED_HASH, seed_observed_at="2026-10-29T08:00:00+09:00", seed_history_unchanged=True)
    return params


def official(day="2026-10-28", gid="new1", home="LG", away="KT", hg=2, ag=1):
    return dict(source_fixture_id=gid, league="KBO", date=day, home=home, away=away,
                hg=hg, ag=ag, available_at=day + "T22:00:00+09:00",
                availability_basis="official_final_observed_at")


def candidate_args(state=None):
    state = kbo.build_state(**build_args(results=[official()])) if state is None else state
    return dict(fixture=target(), decision_at="2026-10-29T17:00:00+09:00", state=state,
                reference=dict(recipe="fixed91_same_electorate", home_vote_share=0., voters=1,
                               observed_at="2026-10-29T16:00:00+09:00", snapshot_id="s1"),
                offers=[dict(side=side, selection_id=side, offer_id="o-" + side, odds=price,
                             market_probability=market, observed_at="2026-10-29T16:00:00+09:00",
                             snapshot_id="s1", market="승패", line=None, label="")
                        for side, price, market in [("home", 1.8, .52), ("away", 1.9, .48)]])


def test_seed_plus_official_history_is_pure_and_order_independent():
    rows = [official(), official(gid="new2", hg=1, ag=7), official(gid="new3", home="한화", away="KIA")]
    args = build_args(results=rows)
    original = deepcopy(args)
    state = kbo.build_state(**args)
    assert state["status"] == "available", state
    assert state["seed_historical_availability"] == "unproven"
    assert state["feature"]["total_observations"] == 2916
    assert state["feature"]["latest_observation"] == "2026-10-28"
    assert args == original
    shuffled = deepcopy(args)
    random.Random(81).shuffle(shuffled["results"])
    assert kbo.build_state(**shuffled) == state
    call = candidate_args(state)
    before = deepcopy(call)
    result = kbo.infer_candidate(**call)
    assert result["status"] == "available", result
    expected = kbo._probability(state["feature"]["x"][0], .9 * .52, kbo.artifact()["final_fit"])
    assert result["home_probability"] == expected
    assert result["fit"] == "final_fit"
    assert result["affects_selection"] is result["affects_probability"] is result["promoted"] is False
    assert call == before
    call["offers"].reverse()
    assert kbo.infer_candidate(**call) == result


def test_daily_cache_binds_later_same_day_without_history_or_file_reads(monkeypatch):
    args = build_args(results=[official()])
    daily = kbo.build_daily_state(state_date=args["fixture"]["date"], as_of="2026-10-29T09:00:00+09:00",
                                 results=args["results"], source_signature=args["source_signature"], seed=args["seed"])
    assert daily["status"] == "available", daily
    before = deepcopy(daily)
    def forbidden(*args, **kwargs):
        raise AssertionError("per-fixture artifact or history IO")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    later = kbo.state_for_fixture(daily_state=daily, fixture=target(), decision_at=args["decision_at"])
    assert later["status"] == "available", later
    assert later["feature"] == kbo.build_state(**args)["feature"]
    assert kbo.infer_candidate(**candidate_args(later))["status"] == "available"
    another = kbo.state_for_fixture(daily_state=daily, fixture=target(home="KIA", away="삼성"), decision_at=args["decision_at"])
    assert another["status"] in ("available", "unavailable")
    if another["status"] == "available":
        assert another["feature"]["x"] != later["feature"]["x"]
    else:
        assert another["reason"] == "stale_team_state"
    assert daily == before
    assert kbo.state_for_fixture(daily_state=daily, fixture=target(), decision_at="2026-10-29T08:59:59+09:00")["reason"] == "daily_state_after_decision"
    assert kbo.state_for_fixture(daily_state=daily, fixture=target(day="2026-10-30"), decision_at="2026-10-30T09:00:00+09:00")["reason"] == "daily_state_date_or_league_mismatch"
    daily["e0"]["mean"]["LG"] += 1
    assert kbo.state_for_fixture(daily_state=daily, fixture=target(), decision_at=args["decision_at"])["reason"] == "daily_state_signature_mismatch"


def coverage_args():
    return dict(schedule_days=[dict(date="2026-10-28", league="KBO", source="kbo_official_calendar",
                                    complete=True, observed_at="2026-10-29T08:00:00+09:00",
                                    source_sha256="a" * 64,
                                    games=[dict(source_fixture_id="new1", home="LG", away="KT", status="final", hg=2, ag=1),
                                           dict(source_fixture_id="postponed", home="KIA", away="한화", status="postponed")])],
                results=[official()], coverage_from="2026-10-28", complete_through="2026-10-28",
                as_of="2026-10-29T09:00:00+09:00", source_id="test-calendar-raw-hash-bound-export")


def test_coverage_helper_returns_auditable_signature_not_blind_verified_flag():
    args = coverage_args()
    before = deepcopy(args)
    result = kbo.verify_increment_coverage(**args)
    assert result["status"] == "available", result
    assert args == before
    source = result["source_signature"]
    assert source["coverage_verified"] is True
    assert source["coverage_evidence_hash"] == kbo.signature(result["coverage_evidence"])
    params = build_args(results=args["results"])
    source.update({k: params["source_signature"][k] for k in ("seed_hash", "seed_observed_at", "seed_history_unchanged")})
    params["source_signature"] = source
    assert kbo.build_state(**params)["status"] == "available"
    # An explicitly complete, observed empty calendar day is valid.
    args["schedule_days"][0]["games"] = []
    args["results"] = []
    assert kbo.verify_increment_coverage(**args)["status"] == "available"
    args["schedule_days"] = []
    assert kbo.verify_increment_coverage(**args)["reason"] == "missing_schedule_day:2026-10-28"


def test_tomorrow_prior_and_decision_before_midnight_after_all_finals_are_observed():
    # Parent-reported official score shape; receipt clocks here are synthetic.
    row = official(day="2026-10-29", gid="official-20261029", home="KIA", away="삼성", hg=4, ag=6)
    row["available_at"] = "2026-10-29T22:55:00+09:00"
    empty = coverage_args()["schedule_days"][0]
    empty["games"] = []
    final = deepcopy(empty)
    final.update(date="2026-10-29", observed_at="2026-10-29T22:55:00+09:00",
                 games=[dict(source_fixture_id=row["source_fixture_id"], home="KIA", away="삼성", status="final", hg=4, ag=6)])
    covered = kbo.verify_increment_coverage(schedule_days=[final, empty], results=[row], coverage_from="2026-10-28",
                                             complete_through="2026-10-29", as_of="2026-10-29T22:55:00+09:00", source_id="synthetic-future-coverage")
    assert covered["status"] == "available", covered
    source = covered["source_signature"]
    source.update(seed_hash=kbo.SEED_HASH, seed_observed_at="2026-10-29T22:54:00+09:00", seed_history_unchanged=True)
    daily = kbo.build_daily_state(state_date="2026-10-30", as_of="2026-10-29T22:55:00+09:00",
                                 results=[row], source_signature=source, seed=kbo.historical_seed())
    assert daily["status"] == "available", daily
    fixture = target(day="2026-10-30", home="KIA", away="삼성")
    decision = "2026-10-29T22:56:00+09:00"
    state = kbo.state_for_fixture(daily_state=daily, fixture=fixture, decision_at=decision)
    assert state["status"] == "available", state
    assert state["feature"]["latest_observation"] == "2026-10-29"
    assert kbo.state_for_fixture(daily_state=daily, fixture=fixture, decision_at="2026-10-29T22:55:00+09:00")["reason"] == "input_not_available_before_decision"
    args = candidate_args()
    args.update(fixture=fixture, decision_at=decision, state=state)
    args["reference"]["observed_at"] = row["available_at"]
    for offer in args["offers"]:
        offer["observed_at"] = row["available_at"]
    assert kbo.infer_candidate(**args)["status"] == "available"
    # Reusing tomorrow's prior for today's fixture would leak today's result.
    assert kbo.state_for_fixture(daily_state=daily, fixture=target(home="KIA", away="삼성"), decision_at=decision)["reason"] == "daily_state_date_or_league_mismatch"


@pytest.mark.parametrize("change,reason", [
    (lambda a: a["schedule_days"][0].update(complete=False), "schedule_day_not_complete"),
    (lambda a: a["schedule_days"][0].update(source="unofficial"), "unsupported_schedule_source"),
    (lambda a: a["schedule_days"][0].update(source_sha256=""), "missing_schedule_source_hash"),
    (lambda a: a["schedule_days"][0].update(observed_at="2026-10-27T23:59:59+09:00"), "schedule_observation_outside_window"),
    (lambda a: a["schedule_days"][0].update(observed_at="2026-10-29T09:00:01+09:00"), "schedule_observation_outside_window"),
    (lambda a: a["schedule_days"][0]["games"][0].update(status="started"), "schedule_game_not_terminal:new1"),
    (lambda a: a["schedule_days"][0]["games"][0].update(away="삼성"), "schedule_result_fixture_mismatch:new1"),
    (lambda a: a["schedule_days"][0]["games"][0].update(hg=3), "schedule_result_score_mismatch:new1"),
    (lambda a: a["schedule_days"][0]["games"][0].update(status="cancelled"), "result_for_unplayed_schedule_game:new1"),
    (lambda a: a.update(results=[]), "missing_final_result:new1"),
    (lambda a: a["results"].append(official(gid="extra")), "unexpected_result_outside_schedule"),
    (lambda a: a["schedule_days"].append(deepcopy(a["schedule_days"][0])), "duplicate_or_outside_schedule_day"),
    (lambda a: a.update(coverage_from="2026-10-27"), "missing_schedule_day:2026-10-27"),
])
def test_coverage_reconciliation_rejects_gaps_unfinished_or_mismatched_games(change, reason):
    args = coverage_args()
    change(args)
    result = kbo.verify_increment_coverage(**args)
    assert result["status"] == "unavailable" and result["reason"] == reason


@pytest.mark.parametrize("change,reason", [
    (lambda a: a["source_signature"].update(coverage_verified=False), "history_completeness_unproven"),
    (lambda a: a["source_signature"].update(coverage_evidence_hash=""), "missing_coverage_evidence_hash"),
    (lambda a: a["source_signature"].update(complete_through="2026-10-27"), "history_coverage_not_previous_day"),
    (lambda a: a["source_signature"].update(coverage_from="2025-01-01"), "history_origin_not_proven"),
    (lambda a: a["source_signature"].update(seed_history_unchanged=False), "seed_correction_requires_full_rebuild"),
    (lambda a: a["source_signature"].update(seed_observed_at="2026-10-29T17:00:00+09:00"), "input_not_available_before_decision"),
    (lambda a: a["source_signature"].update(seed_observed_at="2026-10-27T17:00:00+09:00"), "seed_observation_before_history_end"),
    (lambda a: a["source_signature"].update(seed_observed_at="2026-10-29T08:00:00"), "aware_timestamp_required"),
    (lambda a: a.update(decision_at="2026-10-30T17:00:00+09:00"), "decision_date_mismatch"),
    (lambda a: a["seed"]["mean"].update(LG=99.), "unsupported_seed"),
    (lambda a: a["source_signature"].update(records_hash="fake"), "result_signature_mismatch"),
    (lambda a: a["results"][0].update(available_at="2026-10-29T17:00:00+09:00"), "input_not_available_before_decision"),
    (lambda a: a["results"][0].update(availability_basis=kbo.ARCHIVE_POLICY), "unproven_result_availability"),
    (lambda a: a["results"][0].update(date="2026-10-29"), "result_outside_history_window"),
    (lambda a: a["results"][0].update(date="2026-10-27"), "result_outside_history_window"),
    (lambda a: a["results"][0].update(hg=-1), "invalid_final_score"),
    (lambda a: a["results"][0].update(league="MLB"), "unsupported_league"),
    (lambda a: a["results"].append(deepcopy(a["results"][0])), "duplicate_result_identity"),
])
def test_unproven_missing_or_leaky_state_fails_closed(change, reason):
    args = build_args(results=[official()])
    change(args)
    if reason == "input_not_available_before_decision":
        args["source_signature"]["records_hash"] = kbo.signature(sorted(args["results"], key=lambda r: (r["date"], r["source_fixture_id"])))
    result = kbo.build_state(**args)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert "feature" not in result and "probability" not in result


def test_previous_year_or_stale_state_cannot_be_claimed_current():
    result = kbo.build_state(**build_args(day="2027-03-01"))
    assert result["reason"] == "missing_current_season_team_history"
    result = kbo.build_state(**build_args(day="2026-12-01"))
    assert result["reason"] == "stale_team_state"


def test_full_raw_history_reproduces_seed_and_rebuilds_corrections():
    games = read("archived-games.jsonl.gz")
    # Synthetic explicit observations prove API causality, NOT historical truth.
    rows = [dict(r, league="KBO", available_at=r["date"] + "T23:59:59+09:00",
                 availability_basis="official_final_observed_at") for r in games]
    args = build_args(results=rows, seed=False)
    result = kbo.build_state(**args)
    assert result["status"] == "available", result
    seeded = kbo.build_state(**build_args())
    assert result["feature"]["x"] == pytest.approx(seeded["feature"]["x"], abs=1e-12, rel=0)
    corrected_row = next(r for r in reversed(args["results"]) if "LG" in (r["home"], r["away"]))
    corrected_row["hg"], corrected_row["ag"] = (0, 30) if corrected_row["hg"] > corrected_row["ag"] else (30, 0)
    args["source_signature"]["records_hash"] = kbo.signature(sorted(args["results"], key=lambda r: (r["date"], r["source_fixture_id"])))
    corrected = kbo.build_state(**args)
    assert corrected["status"] == "available"
    assert corrected["state_hash"] != result["state_hash"]
    assert corrected["feature"]["x"] != result["feature"]["x"]


@pytest.mark.parametrize("change,reason", [
    (lambda a: a["reference"].update(voters=0), "missing_eligible_votes"),
    (lambda a: a["reference"].update(recipe="market_only"), "unsupported_reference_recipe"),
    (lambda a: a["reference"].update(home_vote_share=1.01), "invalid_vote_share"),
    (lambda a: a["reference"].update(observed_at="2026-10-29T17:01:00+09:00"), "reference_after_decision"),
    (lambda a: a["reference"].update(snapshot_id="s2"), "snapshot_mismatch"),
    (lambda a: a["offers"][0].update(offer_id="o-away"), "duplicate_or_missing_offer_identity"),
    (lambda a: a["offers"][0].update(market="핸디캡"), "unsupported_market"),
    (lambda a: a["offers"][0].update(odds=1.), "invalid_odds"),
    (lambda a: a["offers"][0].update(observed_at="2026-10-29T18:00:00+09:00"), "offer_after_decision"),
    (lambda a: a["offers"][0].update(market_probability=.7), "market_probability_sum"),
    (lambda a: a["fixture"].update(kickoff="2026-10-29T16:00:00+09:00"), "decision_not_before_kickoff"),
    (lambda a: a["fixture"].update(kickoff="2026-10-29T18:30:00"), "aware_timestamp_required"),
    (lambda a: a["fixture"].update(away="KIA"), "state_fixture_or_time_mismatch"),
    (lambda a: a["state"]["feature"].update(x=[.9]), "state_signature_mismatch"),
])
def test_same_snapshot_inference_rejects_bad_inputs(change, reason):
    args = candidate_args()
    change(args)
    result = kbo.infer_candidate(**args)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert "probability" not in result and "selection_id" not in result


def proto(**changes):
    row = dict(source="proto", season=2026, round=60, game_no="10", kickoff="2026-10-28T18:30:00",
               league="KBO", home_team="LG트윈스", away_team="KT위즈", home_score=5, away_score=2,
               observed_at="2026-10-28T22:00:00+09:00", result_state="confirmed")
    row.update(changes)
    return row


def normalized(rows):
    return kbo.normalize_proto_history(rows, decision_at="2026-10-29T17:00:00+09:00",
                                       aliases={"LG트윈스": "LG", "KT위즈": "KT"})


def test_actual_match_results_revision_contract_and_naive_kst_kickoff():
    first = proto()
    correction = proto(home_score=1, observed_at="2026-10-29T08:00:00+09:00")
    future = proto(home_score=3, observed_at="2026-10-29T17:00:00+09:00")
    result = normalized([future, correction, first])
    assert result["status"] == "available", result
    assert result["records"][0]["hg"] == 1
    assert result["ignored_future_revisions"] == 1
    assert normalized([first, future, correction]) == result
    args = build_args(results=result["records"])
    args["source_signature"]["records_hash"] = result["records_hash"]
    assert kbo.build_state(**args)["status"] == "available"
    invalidated = normalized([first, proto(result_state="unconfirmed", observed_at="2026-10-29T08:00:00+09:00")])
    assert invalidated["reason"] == "latest_result_not_confirmed"
    conflict = normalized([first, proto(home_score=1)])
    assert conflict["reason"] == "conflicting_equal_time_result"
    assert normalized([proto(source="dataset")])["reason"] == "unsupported_result_source"
    assert normalized([proto(observed_at="2026-10-28T18:00:00+09:00")])["reason"] == "final_observed_not_after_kickoff"
    assert normalized([proto(observed_at="2026-10-28T22:00:00")])["reason"] == "aware_timestamp_required"
    assert normalized([first, proto(game_no="11")])["reason"] == "duplicate_physical_fixture"
    doubleheader = normalized([first, proto(game_no="11", kickoff="2026-10-28T14:00:00")])
    assert doubleheader["status"] == "available" and len(doubleheader["records"]) == 2


def test_day_batch_uses_common_prior_even_for_doubleheaders_and_opponents():
    state = kbo._E0State()
    state.advance("2026-10-28")
    before = state.observe("LG", "KT")
    rows = [official(gid="a", hg=20, ag=1), official(gid="b", hg=1, ag=9)]
    state.update(rows)
    delta = .08 * 2 * (1 - kbo._sigmoid(.12)) + .08 * (0 - kbo._sigmoid(.12)) * min(2., math.log1p(8) / math.log(4))
    assert state.mean["LG"] == pytest.approx(delta)
    assert before["x"] == [0.]
    assert state.counts["LG"] == 2
    reversed_state = kbo._E0State()
    reversed_state.advance("2026-10-28")
    reversed_state.update(list(reversed(rows)))
    assert state.seed() == reversed_state.seed()
    old = state.mean["LG"]
    state.advance("2027-01-02")
    gap = (date(2027, 1, 2) - date(2026, 10, 28)).days
    assert state.mean["LG"] == pytest.approx(old * 2 ** (-gap / 120) * .35)
    weak, strong = kbo._E0State(), kbo._E0State()
    for instance in (weak, strong):
        instance.advance("2026-10-28")
    strong.mean["KT"] = 1.
    weak.update([official()])
    strong.update([official()])
    assert strong.mean["LG"] > weak.mean["LG"]


def test_draw_update_and_refund_do_not_create_a_win_or_loss():
    state = kbo._E0State()
    state.advance("2026-10-28")
    state.update([official(hg=3, ag=3)])
    delta = .08 * (.5 - kbo._sigmoid(.12))
    assert state.mean["LG"] == pytest.approx(delta)
    assert state.mean["KT"] == pytest.approx(-delta)
    assert state.total == state.counts["LG"] == state.counts["KT"] == 1
    row = dict(hg=3, ag=3, prices=[1.8, 1.9], order=["home", "away"])
    assert settle(row, .7) == settle(row, .3) == (0, 0, 0)


def test_historical_features_cannot_become_prospective_by_renaming_timestamps():
    row = read("archived-reproduction.jsonl.gz")[-1]
    signature = kbo.artifact()["archive_sources"]
    expected = kbo.infer_archived(row, source_signature=signature, partition="evaluation")
    row.update(hg=10000, ag=0, winner="home", observed_at="2020-01-01T00:00:00+09:00")
    assert kbo.infer_archived(row, source_signature=signature, partition="evaluation") == expected
    assert kbo.infer_archived(row, source_signature={}, partition="evaluation")["reason"] == "archive_source_signature_mismatch"
    assert kbo.infer_candidate(**candidate_args(expected))["status"] == "unavailable"
    row["dynamic"]["e0"]["latest_observation"] = row["date"]
    assert kbo.infer_archived(row, source_signature=signature, partition="evaluation")["reason"] == "archived_same_day_result_leak"


def test_bounded_iterator_and_small_future_state_memory():
    def oversized():
        for i in range(kbo.MAX_RESULTS + 1):
            yield official(gid=str(i))
    args = build_args()
    args["results"] = oversized()
    assert kbo.build_state(**args)["reason"] == "result_limit_exceeded"
    tracemalloc.start()
    for _ in range(50):
        assert kbo.infer_candidate(**candidate_args())["status"] == "available"
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1_000_000
