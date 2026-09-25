from copy import deepcopy
from datetime import timedelta
import pytest

from test_recommendation_history import NOW, candidate
from shadow_combo import update_experiment


def feed(now, rows=None):
    return {"generated_at": now.isoformat(), "source_generated_at": now.isoformat(),
            "live_odds_at": now.isoformat(), "candidates": rows if rows is not None else
            [candidate(i, predicted_hit_prob=.65, probability_source="shin_market_fallback") for i in (1, 2)]}


def draft():
    now = NOW + timedelta(hours=2, minutes=20)
    return update_experiment(feed(now), None, {}, now)


def test_freeze_original_offer_and_no_backfill():
    state = draft()
    original = deepcopy(state)
    later = NOW + timedelta(hours=4)
    frozen = update_experiment(feed(later), state, {}, later)
    day = frozen["days"]["2026-09-06"]
    assert day["status"] == "frozen"
    assert day["singles"] == original["days"]["2026-09-06"]["singles"]
    assert len(day["legs"]) == 2
    assert day["independence_assumed_ev"] == pytest.approx(.65 ** 2 * 1.6 ** 2 - 1)
    assert state == original
    assert update_experiment(feed(later), None, {}, later)["days"] == {}


def test_stale_at_cutoff_skips_even_if_writer_runs_late():
    state = update_experiment(feed(NOW), None, {}, NOW)
    out = update_experiment(feed(NOW), state, {}, NOW + timedelta(hours=4))
    assert next(iter(out["days"].values()))["status"] == "skipped_stale"
    assert out["summary"]["two_leg"]["settled"] == 0


def test_freshness_missing_future_and_duplicate_event():
    for field in ("generated_at", "source_generated_at", "live_odds_at"):
        for value in (None, (NOW+timedelta(seconds=1)).isoformat(), (NOW-timedelta(minutes=16)).isoformat()):
            p = feed(NOW)
            p[field] = value
            assert update_experiment(p, None, {}, NOW)["days"] == {}
    p = feed(NOW, [candidate(predicted_hit_prob=.7), candidate(game_no=2, predicted_hit_prob=.65)])
    day = next(iter(update_experiment(p, None, {}, NOW)["days"].values()))
    assert len(day["singles"]) == 1
    assert day["legs"] == []
    assert next(iter(day["singles"].values()))["probability_source"] == "unknown"


def test_official_settlement_void_pending_and_feed_expiry():
    later = NOW + timedelta(hours=4)
    prices = {"markets": {"1": {str(i): {**candidate(i), "label": "", "result": r}
                               for i, r in ((1, "홈승"), (2, "무효"))}}}
    state = update_experiment(feed(later), draft(), prices, later)
    assert state["summary"]["two_leg"]["roi"] == pytest.approx(.6)
    assert state["summary"]["filtered_singles"]["settled"] == 2
    assert update_experiment(feed(later), state, {}, later)["summary"] == state["summary"]
    prices["markets"]["1"]["2"]["result"] = "홈패"
    state = update_experiment(feed(later), state, prices, later)
    assert state["summary"]["two_leg"]["roi"] == -1
    assert state["summary"]["two_leg"]["max_drawdown_units_cohort_order"] == 1


def test_empty_partial_preserves_draft_and_deadline_never_moves_later():
    state = draft()
    now = NOW + timedelta(hours=2, minutes=25)
    assert update_experiment(feed(now, []), state, {}, now)["days"] == state["days"]
    partial = feed(now, [candidate(3, predicted_hit_prob=.9)])
    partial["partial"] = True
    assert update_experiment(partial, state, {}, now)["days"] == state["days"]
    rows = [candidate(i, predicted_hit_prob=.7, kickoff_at="2026-09-06T14:00:00+09:00") for i in (3,4)]
    out = update_experiment(feed(now, rows), state, {}, now)
    assert out["days"]["2026-09-06"]["cutoff_at"] == state["days"]["2026-09-06"]["cutoff_at"]


def test_database_ignores_injected_shadow_and_preserves_saved_state(tmp_path):
    from datetime import datetime, timezone
    from runtime_db import RuntimeDatabase
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [candidate(i, predicted_hit_prob=.65, kickoff_at=(now+timedelta(hours=2)).isoformat()) for i in (1,2)]
    db = RuntimeDatabase(tmp_path / "shadow.sqlite")
    p = feed(now, rows)
    p["shadow_experiment"] = {"forged": True}
    db.store_artifact("today_combo", p)
    saved = db.get_artifact("today_combo")["shadow_experiment"]
    assert saved["paper_only"] is True
    assert len(saved["days"]) == 1
    db.store_artifact("today_combo", feed(now, []))
    assert db.get_artifact("today_combo")["shadow_experiment"] == saved


def test_cutoff_boundary_and_pending_counts():
    at_cutoff = NOW + timedelta(hours=2, minutes=30)
    changed = feed(at_cutoff, [candidate(3, predicted_hit_prob=.9)])
    state = update_experiment(changed, draft(), {}, at_cutoff)
    assert len(next(iter(state["days"].values()))["legs"]) == 2
    assert state["summary"]["two_leg"]["pending"] == 1
    assert state["summary"]["two_leg"]["roi"] is None
    assert update_experiment(changed, None, {}, at_cutoff)["days"] == {}


def test_equal_retention_and_version_guard():
    state = draft()
    later = NOW + timedelta(days=91)
    assert update_experiment({}, state, {}, later)["days"] == {}
    state["policy"] = "older-policy"
    assert update_experiment(feed(NOW), state, {}, NOW) == state
