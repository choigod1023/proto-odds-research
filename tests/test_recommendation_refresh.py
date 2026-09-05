import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import recommendation_refresh


def test_signature_ignores_price_only_changes_but_detects_pick_changes():
    base = {"recommendation": {"action": "challenge", "recommended_target": 3},
            "solo": None, "plans": [{"ok": True, "target": 3, "picks": [
                {"round": 1, "game_no": 2, "market": "승패", "market_label": "", "sel": "홈", "odds": 1.7}
            ]}]}
    price = json.loads(json.dumps(base))
    price["plans"][0]["picks"][0]["odds"] = 1.75
    assert recommendation_refresh.recommendation_signature(base) == recommendation_refresh.recommendation_signature(price)
    price["plans"][0]["picks"][0]["sel"] = "원정"
    assert recommendation_refresh.recommendation_signature(base) != recommendation_refresh.recommendation_signature(price)


def test_reason_distinguishes_status_changes():
    old = {"recommendation": {"action": "pass", "recommended_target": 3}, "plans": []}
    status = {"recommendation": {"action": "challenge", "recommended_target": 3}, "plans": []}
    assert recommendation_refresh._reason(old, status) == "recommendation_status_changed"


def _daily_payload():
    return {"recommendation": {"action": "disabled"}, "n_candidates": 1,
            "daily_recommendation_policy": {"policy_version": "daily-value-v1"},
            "candidates": [{"round": 105, "game_no": "17", "market": "승패",
                            "sel": "홈", "odds": 1.8,
                            "daily_recommendation": {"recommended": True,
                                "probability": .52, "comparison_return": -.064,
                                "validated_interval": False, "league_rank": 1}}]}


def test_daily_values_and_membership_change_revision_even_without_combos():
    original = _daily_payload()
    for field, value in [("comparison_return", -.08), ("recommended", False),
                         ("league_rank", 2), ("probability", .51)]:
        changed = json.loads(json.dumps(original))
        changed["candidates"][0]["daily_recommendation"][field] = value
        assert recommendation_refresh._reason(original, changed) == "daily_value_changed"
    changed = json.loads(json.dumps(original))
    changed["candidates"][0]["odds"] = 1.75
    assert recommendation_refresh._reason(original, changed) == "daily_value_changed"
    changed["daily_recommendation_policy"]["policy_version"] = "daily-value-v2"
    assert recommendation_refresh._reason(original, changed) == "daily_policy_changed"
    unchanged = {**original, "generated_at": "different", "refreshed_at": "later"}
    assert recommendation_refresh.recommendation_signature(original) == (
        recommendation_refresh.recommendation_signature(unchanged))


def test_daily_value_refresh_persists_database_artifact_and_revision(monkeypatch, tmp_path):
    from runtime_db import RuntimeDatabase

    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    payload = _daily_payload()
    monkeypatch.setattr(recommendation_refresh.today_combo, "build",
                        lambda: json.loads(json.dumps(payload)))
    first = recommendation_refresh.refresh()
    assert first["changed"] is True
    db = RuntimeDatabase()
    stored = db.get_artifact("today_combo")
    assert stored["candidates"][0]["daily_recommendation"]["comparison_return"] == -.064
    assert recommendation_refresh.refresh()["changed"] is False
    payload["candidates"][0]["daily_recommendation"]["recommended"] = False
    second = recommendation_refresh.refresh()
    assert second["changed"] is True
    assert first["revision"] != second["revision"]
    assert db.get_artifact("today_combo")["last_recommendation_change"]["reason"] == "daily_value_changed"
