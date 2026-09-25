from copy import deepcopy
from datetime import timedelta

from test_recommendation_history import candidate, NOW
from per_event_shadow import capture, proposals, eligibility


def validated(**kw):
    return candidate(predicted_hit_prob=.75, probability_lower_bound=.7,
                     has_validated_edge=True, decision_pipeline_applied=True,
                     validated_uncertainty_available=True, decision_id="d1",
                     decision_artifact_hash="hash", **kw)


def payload(rows, baseline=None, now=NOW):
    return {"generated_at": now.isoformat(), "source_generated_at": now.isoformat(),
            "live_odds_at": now.isoformat(),
            "per_event_shadow_proposals": proposals(rows, baseline or rows)}


def test_value_gate_and_probability_ranking():
    a = validated(odds=1.45)
    b = validated(odds=1.7, sel="원정")
    b["predicted_hit_prob"] = .71
    pool = [a, b]
    before = deepcopy(pool)
    proposal = proposals(pool, [b])[0]
    assert proposal["challenger"]["odds"] == 1.45
    assert proposal["baseline"]["odds"] == 1.7
    assert pool == before
    assert eligibility(candidate()) == "unvalidated_probability"
    assert eligibility(validated(odds=1.2)) == "insufficient_conservative_value"
    a["probability_lower_bound"] = .9
    assert eligibility(a) == "invalid_probability_or_price"


def test_first_observation_immutable_and_no_backfill():
    data = payload([validated()])
    state = capture(data, {}, {}, NOW)
    newer = payload([validated(sel="원정")], now=NOW+timedelta(minutes=1))
    again = capture(newer, {"per_event_shadow": state}, {}, NOW+timedelta(minutes=1))
    assert again["records"] == state["records"]
    assert capture(data, {}, {}, NOW+timedelta(hours=3))["status"] == "awaiting_fresh_source"
    late = payload([validated()], now=NOW+timedelta(hours=3))
    assert capture(late, {}, {}, NOW+timedelta(hours=3))["observed_events"] == 0


def test_stale_partial_future_and_recovery():
    for changes in ({"partial": True}, {"error": "failed"}, {"live_odds_at": None},
                    {"source_generated_at": (NOW+timedelta(minutes=1)).isoformat()}):
        state = capture({**payload([validated()]), **changes}, {}, {}, NOW)
        assert state["status"] == "awaiting_fresh_source"
        assert capture(payload([validated()]), {"per_event_shadow": state}, {}, NOW)["observed_events"] == 1


def test_abstention_and_official_settlement():
    state = capture(payload([candidate()]), {}, {}, NOW)
    assert state["abstained_events"] == 1
    assert state["summary"]["challenger"]["roi"] is None
    odds = {"markets": {"1": {"1": {**candidate(), "label": "", "result": "홈승"}}}}
    end = NOW+timedelta(hours=5)
    state = capture({}, {"per_event_shadow": state}, odds, end)
    assert abs(state["summary"]["baseline"]["roi"]-.6) < 1e-9
    assert state["source_status"] == "stale_or_partial"
    state = capture({}, {"per_event_shadow": state}, {}, end+timedelta(days=30))
    assert state["status"] == "closed_settling"
    assert state["observed_events"] == 1


def test_database_ignores_incoming_archive(tmp_path):
    from datetime import datetime, timezone
    from runtime_db import RuntimeDatabase
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row = validated(kickoff_at=(now+timedelta(hours=3)).isoformat())
    db = RuntimeDatabase(tmp_path / "shadow.sqlite3")
    data = payload([row], now=now)
    data["per_event_shadow"] = {"policy": "forged", "records": {"fake": {}}}
    db.store_artifact("today_combo", data)
    result = db.get_artifact("today_combo")["per_event_shadow"]
    assert result["policy"] != "forged"
    assert result["observed_events"] == 1


def test_research_pool_does_not_change_operational_selection(monkeypatch):
    from test_daily_candidates import loader, feed, NOW as feed_now
    from src import today_combo
    loader(monkeypatch, feed())
    source = today_combo._candidate_source()
    before = today_combo.legs_today(feed_now, source=source)
    research = today_combo.legs_today(feed_now, source=source, research=True)
    assert {r["sel"] for r in research} == {"홈", "원정"}
    assert any(r["odds"] >= 2.2 for r in research)
    assert today_combo.legs_today(feed_now, source=source) == before
    assert [r["sel"] for r in before] == ["홈"]


def test_closed_window_never_registers_new_event():
    state = capture(payload([validated()]), {}, {}, NOW)
    later = NOW+timedelta(days=29)
    row = validated(home="new", kickoff_at=(later+timedelta(hours=3)).isoformat())
    closed = capture(payload([row], now=later), {"per_event_shadow": state}, {}, later)
    assert closed["observed_events"] == 1
    assert closed["status"] == "closed_settling"
