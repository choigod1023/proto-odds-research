from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.compare_pregame_research import compare, pair_hash, read_jsonl

DAY = datetime(2026, 10, 1, tzinfo=timezone.utc)
AS_OF = "2026-10-31T23:00:00+09:00"


def record(i, *, day=DAY, probability=.6, result="hit", sport="bs", event=None):
    pair = {"id": i, "event_id": event or f"event-{i}", "snapshot_id": f"snapshot-{i}",
            "selection_id": "home", "kickoff": (day + timedelta(hours=3)).isoformat(),
            "observed_at": day.isoformat(), "captured_at": day.isoformat(),
            "source": {"name": "runtime_prediction_ledger"}, "odds": {"decimal": 2.0},
            "baseline_decision": {"sport": sport, "round": 100, "probability": probability}}
    pair["datahash"] = pair_hash(pair)
    results = []
    if result:
        results = [{"event_id": pair["event_id"], "snapshot_id": pair["snapshot_id"],
                    "selection_id": "home", "source": {"name": "proto_official"},
                    "outcome": {"result": result, "selection_id": "home"},
                    "observed_at": (day + timedelta(hours=5)).isoformat(),
                    "captured_at": (day + timedelta(hours=5)).isoformat()}]
    return {"pair": pair, "evidence": [], "results": results, "complete": False,
            "incomplete_flags": ["missing_pregame_evidence"], "official_outcome": None}


def forecast(record, p=.8, model="test-model"):
    pair = record["pair"]
    observation = datetime.fromisoformat(pair["observed_at"])
    return {"pair_id": pair["id"], "pair_datahash": pair["datahash"], "model_id": model,
            "probability": p, "training_cutoff": (observation - timedelta(days=10)).isoformat(),
            "predicted_at": (observation + timedelta(minutes=1)).isoformat(),
            "features_as_of": pair["observed_at"],
            "provenance": {"protocol": "temporal_holdout_v1", "artifact_sha256": "a" * 64,
                           "config_sha256": "b" * 64}}


def test_baseline_only_no_invented_challenger_or_improvement():
    report = compare([record(1), record(2, result=None)], as_of=AS_OF)
    assert report["models"] == {} and report["improvement_roi"] is None
    assert report["baseline_by_sport"]["bs"]["opportunities"] == 2
    assert report["baseline_by_sport"]["bs"]["pending"] == 1
    for wallet in report["baseline_wallets"].values():
        assert wallet["tickets"] == 1 and wallet["pending_stake"] == 10000
        assert wallet["roi"] is None


def test_latest_pair_selection_does_not_choose_resolved_old_snapshot():
    old = record(1, event="one", result="hit")
    new = record(2, day=DAY + timedelta(minutes=1), event="one", result=None)
    other = record(3, result="hit")
    a = compare([old, new, other], as_of=AS_OF)
    b = compare([other, new, old], as_of=AS_OF)
    assert a == b
    assert a["selected_pairs"] == 2
    assert a["baseline_by_sport"]["bs"]["pending"] == 1
    assert a["baseline_wallets"]["2"]["pending_stake"] == 10000


def test_brier_same_outcomes_and_separate_model_wallets():
    records = [record(1, probability=.9, result="miss"), record(2, probability=.8),
               record(3, probability=.5, sport="sc")]
    predictions = [forecast(records[0], .1), forecast(records[1], .9), forecast(records[2], .95)]
    report = compare(records, predictions, as_of=AS_OF)
    model = report["models"]["test-model"]
    assert model["accepted"] == 3
    assert model["challenger_by_sport"]["bs"]["hit_rate"] == model["matched_baseline_by_sport"]["bs"]["hit_rate"]
    assert model["challenger_by_sport"]["bs"]["brier"] < model["matched_baseline_by_sport"]["bs"]["brier"]
    assert model["matched_baseline_wallets"]["2"]["net"] == -10000
    assert model["challenger_wallets"]["2"]["net"] == 30000
    assert model["roi_delta"]["2"] == 4
    assert "not_independently_verified" in model["provenance_status"]
    assert report["improvement_roi"] is None  # no selected best model claim


@pytest.mark.parametrize("change", [
    {"pair_datahash": "0" * 64}, {"training_cutoff": DAY.isoformat()},
    {"predicted_at": (DAY + timedelta(hours=10)).isoformat()},
    {"features_as_of": (DAY + timedelta(seconds=1)).isoformat()},
    {"provenance": {"protocol": "random_kfold"}}, {"probability": float("nan")},
])
def test_invalid_challenger_is_not_used(change):
    r = record(1)
    f = forecast(r)
    f.update(change)
    report = compare([r], [f], as_of=AS_OF)
    model = report["models"]["test-model"]
    assert model["accepted"] == 0 and model["roi_delta"] == {"2": None, "3": None}
    assert model["challenger_wallets"] is None and model["rejected"]


def test_no_after_result_forecast_ranking_or_duplicate_pair_conflicts():
    r = record(1)
    with pytest.raises(RuntimeError, match="ranking forbidden"):
        compare([r], [forecast(r), forecast(r, .9)], as_of=AS_OF)
    duplicate = deepcopy(r)
    duplicate["results"] = []
    with pytest.raises(RuntimeError, match="conflicting duplicate"):
        compare([r, duplicate], as_of=AS_OF)


def test_shared_monthly_budget_includes_all_pending_sports():
    records = []
    for day in range(30):
        records += [record(day * 2 + 1, day=DAY + timedelta(days=day), result=None, sport="bs"),
                    record(day * 2 + 2, day=DAY + timedelta(days=day), result=None, sport="sc")]
    report = compare(records, as_of=AS_OF)
    for wallet in report["baseline_wallets"].values():
        assert wallet["tickets"] == 5 and wallet["pending_stake"] == 50000
        assert wallet["net"] == 0 and wallet["roi"] is None
        assert all(t["available_loss_budget_before"] >= 10000 for t in wallet["ticket_log"])


def test_wrong_snapshot_unofficial_future_results_remain_pending():
    for change in ({"snapshot_id": "wrong"}, {"source": "tipster"},
                   {"captured_at": "2027-01-01T00:00:00Z"}):
        r = record(1)
        r["results"][0].update(change)
        report = compare([r], as_of=AS_OF)
        assert report["baseline_by_sport"]["bs"]["pending"] == 1


def test_hash_tamper_and_missing_round_are_reported():
    r = record(1)
    r["pair"]["baseline_decision"]["probability"] = .99
    assert compare([r], as_of=AS_OF)["excluded"]["invalid_or_unavailable_pair"] == 1
    r = record(2)
    r["pair"]["baseline_decision"]["round"] = None
    r["pair"]["datahash"] = pair_hash(r["pair"])
    report = compare([r], as_of=AS_OF)
    assert report["warnings"]["missing_round_wallet_excluded"] == 1
    assert report["baseline_wallets"]["2"]["tickets"] == 0


def test_bounded_jsonl_refuses_oversize(tmp_path):
    file = tmp_path / "records.jsonl"
    file.write_text(json.dumps(record(1)) + "\n", encoding="utf-8")
    assert len(read_jsonl(file)) == 1
    file.write_bytes(b"x" * 262145)
    with pytest.raises(ValueError, match="byte limit"):
        read_jsonl(file)


def test_real_sqlite_export_consumed_without_adapting_hash_or_schema(tmp_path):
    from src.pregame_research_store import PregameResearchStore
    clock = [DAY]
    store = PregameResearchStore(tmp_path / "research.sqlite3", official_sources={"proto_official"},
                                clock=lambda: clock[0])
    fixture = record(1)["pair"]
    saved = store.append_pair(event_id=fixture["event_id"], kickoff=fixture["kickoff"],
        observed_at=DAY, source=fixture["source"], snapshot_id=fixture["snapshot_id"],
        selection_id="home", odds=fixture["odds"], baseline_decision=fixture["baseline_decision"],
        evidence_refs=[])
    clock[0] = DAY + timedelta(hours=5)
    store.append_result(event_id=fixture["event_id"], kickoff=fixture["kickoff"],
        settled_observed_at=clock[0], source={"name": "proto_official"},
        snapshot_id=fixture["snapshot_id"], selection_id="home", outcome={"result": "hit"})
    report = compare(list(store.export_paired()), as_of=AS_OF)
    assert report["selected_pairs"] == 1 and not report["excluded"]
    assert report["selected_pair_refs"][0]["datahash"] == saved["datahash"]
    assert report["baseline_by_sport"]["bs"]["hit_rate"] == 1
    assert report["improvement_roi"] is None
