from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pregame_research_store import PregameResearchStore, ResearchConflict, APPLICATION_ID

START = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)
FACTS = [{"type": "starters", "subject": "home", "value": ["player-1"]}]


@pytest.fixture
def setup(tmp_path):
    clock = [START - timedelta(hours=2)]
    store = PregameResearchStore(tmp_path / "research.sqlite3",
        official_sources={"proto_official", "league_official"}, clock=lambda: clock[0])
    return store, clock


def evidence(store, clock, **changes):
    args = dict(event_id="event-1", kickoff=START, observed_at=clock[0],
                source={"name": "player_info", "url": "https://example.org/roster"}, typed_facts=FACTS)
    args.update(changes)
    return store.append_evidence(**args)


def pair(store, clock, **changes):
    args = dict(event_id="event-1", kickoff=START, observed_at=clock[0], source="baseline",
                snapshot_id="snap-1", selection_id="home-win",
                odds={"home": 1.8, "away": 2.1},
                baseline_decision={"event_id": "event-1", "probability": .6})
    args.update(changes)
    return store.append_pair(**args)


def result(store, clock, **changes):
    args = dict(event_id="event-1", kickoff=START, settled_observed_at=clock[0],
                source={"name": "proto_official", "round": 100, "game_no": 1, "market": "win"},
                snapshot_id="snap-1", selection_id="home-win",
                outcome={"result": "hit", "selection_id": "home-win", "official_result": "home"})
    args.update(changes)
    return store.append_result(**args)


def test_end_to_end_exact_pair_and_source_timestamp(setup):
    store, clock = setup
    old = clock[0] - timedelta(hours=1)
    saved = evidence(store, clock, source_updated_at=old)
    clock[0] += timedelta(seconds=1)
    paired = pair(store, clock)
    clock[0] = START + timedelta(hours=3)
    result(store, clock)
    export = list(store.export_paired())
    assert len(export) == 1 and export[0]["complete"]
    assert export[0]["pair"]["id"] == paired["id"]
    assert export[0]["evidence"][0]["id"] == saved["id"]
    assert saved["source_updated_at"] != saved["observed_at"]
    assert export[0]["official_outcome"]["result"] == "hit"


def test_idempotent_first_observation_and_change_revisions(setup):
    store, clock = setup
    first = evidence(store, clock)
    clock[0] += timedelta(seconds=1)
    assert evidence(store, clock) == first
    changed = evidence(store, clock, typed_facts=[{"type": "unavailable", "subject": "away", "value": ["p2"]}])
    clock[0] += timedelta(seconds=1)
    returned = evidence(store, clock)
    assert [first["revision"], changed["revision"], returned["revision"]] == [1, 2, 3]
    assert returned["datahash"] == first["datahash"]
    assert returned["observed_at"] != first["observed_at"]


def test_same_time_changes_and_out_of_order_fail(setup):
    store, clock = setup
    evidence(store, clock)
    with pytest.raises(ResearchConflict):
        evidence(store, clock, typed_facts=[{"type": "rest", "subject": "home", "value": 2}])
    with pytest.raises(ResearchConflict):
        evidence(store, clock, observed_at=clock[0] - timedelta(seconds=1))


@pytest.mark.parametrize("field", ["kickoff", "observed_at", "source_updated_at"])
def test_naive_times_rejected(setup, field):
    store, clock = setup
    with pytest.raises(ValueError):
        evidence(store, clock, **{field: "2026-10-25T08:00:00"})


def test_future_times_and_source_update_rejected(setup):
    store, clock = setup
    with pytest.raises(ValueError):
        evidence(store, clock, observed_at=clock[0] + timedelta(seconds=1))
    with pytest.raises(ValueError):
        evidence(store, clock, source_updated_at=clock[0] + timedelta(seconds=1))
    with pytest.raises(ValueError):
        result(store, clock)


def test_strict_cutoff_and_no_backfilled_pairs(setup):
    store, clock = setup
    first = pair(store, clock)
    clock[0] = START - timedelta(minutes=30)
    assert pair(store, clock) == first  # repeat cannot move the first time
    with pytest.raises(ValueError):
        pair(store, clock, snapshot_id="new")
    with pytest.raises(ValueError):
        pair(store, clock, snapshot_id="new", observed_at=START - timedelta(hours=2))


def test_immutable_snapshot_and_cross_source_conflicts(setup):
    store, clock = setup
    pair(store, clock)
    clock[0] += timedelta(seconds=1)
    for changes in ({"odds": {"home": 2.0}}, {"source": "other", "odds": {"home": 2.0}}):
        with pytest.raises(ResearchConflict):
            pair(store, clock, **changes)
    with pytest.raises(ResearchConflict):
        pair(store, clock, baseline_decision={"event_id": "event-2"})
    with pytest.raises(ResearchConflict):
        pair(store, clock, kickoff=START + timedelta(hours=1))


@pytest.mark.parametrize("mismatch", [{"event_id": "other"}, {"snapshot_id": "other"},
                                     {"selection_id": "other", "outcome": {"result": "hit"}}])
def test_result_cannot_cross_event_snapshot_or_selection(setup, mismatch):
    store, clock = setup
    pair(store, clock)
    clock[0] = START + timedelta(hours=3)
    result(store, clock, **mismatch)
    exported = list(store.export_paired())[0]
    assert exported["official_outcome"] is None
    assert "missing_official_result" in exported["incomplete_flags"]


def test_official_results_corrections_and_conflicts(setup):
    store, clock = setup
    pair(store, clock)
    clock[0] = START + timedelta(hours=3)
    with pytest.raises(ValueError):
        result(store, clock, source="tipster")
    first = result(store, clock)
    clock[0] += timedelta(seconds=1)
    assert result(store, clock) == first
    correction = result(store, clock, outcome={"result": "miss"})
    assert correction["revision"] == 2
    assert list(store.export_paired())[0]["official_outcome"] == {"result": "miss"}
    result(store, clock, source="league_official")
    exported = list(store.export_paired())[0]
    assert exported["official_outcome"] is None
    assert "conflicting_official_results" in exported["incomplete_flags"]


def test_no_future_evidence_leak_and_no_score_inference(setup):
    store, clock = setup
    pair(store, clock)
    earlier = clock[0]
    clock[0] += timedelta(minutes=1)
    evidence(store, clock, observed_at=earlier)  # captured after the pair
    clock[0] = START + timedelta(hours=3)
    result(store, clock, outcome={"result": "hit"})
    exported = list(store.export_paired())[0]
    assert exported["evidence"] == []
    assert exported["official_outcome"] == {"result": "hit"}
    assert "missing_pregame_evidence" in exported["incomplete_flags"]


def test_payload_limit_nan_and_final_pick_types(setup):
    store, clock = setup
    with pytest.raises(ValueError):
        evidence(store, clock, typed_facts=[{"type": "rotation", "subject": "home", "value": "x" * 65536}])
    with pytest.raises(ValueError):
        pair(store, clock, odds={"home": float("nan")})
    with pytest.raises(ValueError):
        evidence(store, clock, typed_facts=[{"type": "final_pick", "subject": "home", "value": "win"}])
    with pytest.raises(ValueError):
        pair(store, clock, snapshot_id="")


def test_append_only_sql_and_runtime_db_refusal(setup, tmp_path):
    store, clock = setup
    evidence(store, clock)
    with sqlite3.connect(store.path) as db:
        for sql in ("DELETE FROM observations", "UPDATE observations SET revision=9", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    live = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE ledger(value TEXT)")
        db.execute("INSERT INTO ledger VALUES ('immutable')")
    before = live.read_bytes()
    with pytest.raises(ResearchConflict):
        PregameResearchStore(live, official_sources={"proto_official"})
    assert before == live.read_bytes()


def test_short_busy_timeout_no_partial_write(setup):
    store, clock = setup
    with sqlite3.connect(store.path) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            evidence(store, clock)
        assert time.monotonic() - start < 2
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_reopen_preserves_records_and_normalizes_timezone(setup):
    store, clock = setup
    original = evidence(store, clock)
    other = PregameResearchStore(store.path, official_sources={"proto_official"}, clock=lambda: clock[0])
    assert evidence(other, clock, observed_at=clock[0].astimezone(timezone(timedelta(hours=9)))) == original


def test_bounded_pair_pages_for_settlement_capture(setup):
    store, clock = setup
    first = pair(store, clock)
    second = pair(store, clock, snapshot_id="snap-2")
    page = list(store.iter_pairs(limit=1))
    assert page[0]["id"] == first["id"] and "kickoff" in page[0]
    assert [r["id"] for r in store.iter_pairs(after_id=first["id"], limit=1)] == [second["id"]]
    assert len(list(store.export_paired(limit=1))) == 1
    assert list(store.iter_pairs(after_id=second["id"])) == []
    with pytest.raises(ValueError):
        list(store.iter_pairs(limit=1001))
    with pytest.raises(ValueError):
        list(store.export_paired(after_id=-1))


def test_read_only_export_no_writes_or_creation(setup, tmp_path):
    store, clock = setup
    original = pair(store, clock)
    before = store.path.read_bytes()
    readonly = PregameResearchStore(store.path, official_sources={"proto_official"},
                                   read_only=True, clock=lambda: clock[0])
    assert list(readonly.export_paired(limit=100))[0]["pair"]["id"] == original["id"]
    with pytest.raises(PermissionError):
        evidence(readonly, clock)
    with readonly._connect() as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE accidental(value)")
    assert store.path.read_bytes() == before
    missing = tmp_path / "does-not-exist.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        PregameResearchStore(missing, official_sources={"proto_official"}, read_only=True)
    assert not missing.exists()


def test_read_only_can_open_while_another_writer_holds_reserved_lock(setup):
    store, clock = setup
    pair(store, clock)
    with sqlite3.connect(store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        readonly = PregameResearchStore(store.path, official_sources={"proto_official"}, read_only=True)
        assert len(list(readonly.iter_pairs())) == 1


def test_read_only_checks_schema_not_just_application_id(tmp_path):
    wrong = tmp_path / "wrong.sqlite3"
    with sqlite3.connect(wrong) as db:
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("CREATE TABLE events(unexpected TEXT)")
        db.execute("CREATE TABLE observations(unexpected TEXT)")
    before = wrong.read_bytes()
    with pytest.raises(ResearchConflict, match="columns"):
        PregameResearchStore(wrong, official_sources={"proto_official"}, read_only=True)
    assert before == wrong.read_bytes()


def test_new_lineup_creates_research_revision_without_changing_operational_snapshot(setup):
    store, clock = setup
    initial = pair(store, clock, evidence_refs=[])
    clock[0] += timedelta(minutes=1)
    facts = evidence(store, clock)
    refs = [{"id": facts["id"], "datahash": facts["datahash"]}]
    updated = pair(store, clock, evidence_refs=refs)
    assert updated["id"] != initial["id"] and updated["revision"] == 2
    assert updated["snapshot_id"] == initial["snapshot_id"]
    assert updated["baseline_decision"] == initial["baseline_decision"]
    assert updated["odds"] == initial["odds"]
    clock[0] += timedelta(minutes=1)
    assert pair(store, clock, evidence_refs=refs) == updated
    clock[0] = START + timedelta(hours=3)
    result(store, clock)
    exports = list(store.export_paired())
    assert exports[0]["evidence"] == [] and not exports[0]["complete"]
    assert exports[1]["evidence"][0]["id"] == facts["id"] and exports[1]["complete"]
    assert exports[0]["official_outcome"] == exports[1]["official_outcome"]


def test_refs_pin_exact_revision_not_latest_source(setup):
    store, clock = setup
    first = evidence(store, clock)
    clock[0] += timedelta(minutes=1)
    evidence(store, clock, typed_facts=[{"type": "lineups", "subject": "home", "value": ["new"]}])
    pair(store, clock, evidence_refs=[{"id": first["id"], "datahash": first["datahash"]}])
    exported = list(store.export_paired())[0]
    assert exported["evidence"][0]["typed_facts"] == FACTS


def test_refs_reject_other_event_wrong_hash_and_future_context(setup):
    store, clock = setup
    other = evidence(store, clock, event_id="other")
    with pytest.raises(ResearchConflict):
        pair(store, clock, evidence_refs=[{"id": other["id"], "datahash": other["datahash"]}])
    same = evidence(store, clock)
    with pytest.raises(ResearchConflict):
        pair(store, clock, evidence_refs=[{"id": same["id"], "datahash": "f" * 64}])
    with pytest.raises(ResearchConflict):
        pair(store, clock, observed_at=clock[0] - timedelta(seconds=1),
             evidence_refs=[{"id": same["id"], "datahash": same["datahash"]}])


def test_context_change_still_rejects_baseline_changes_and_after_t30(setup):
    store, clock = setup
    pair(store, clock, evidence_refs=[])
    clock[0] += timedelta(minutes=1)
    fact = evidence(store, clock)
    refs = [{"id": fact["id"], "datahash": fact["datahash"]}]
    with pytest.raises(ResearchConflict):
        pair(store, clock, evidence_refs=refs, odds={"decimal": 99})
    clock[0] = START - timedelta(minutes=30)
    with pytest.raises(ValueError):
        pair(store, clock, evidence_refs=refs)
