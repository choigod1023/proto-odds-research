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
