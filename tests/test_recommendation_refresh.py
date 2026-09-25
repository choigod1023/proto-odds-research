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


def test_failure_is_nonzero(monkeypatch):
    def fail():
        raise ValueError('broken source')
    monkeypatch.setattr(recommendation_refresh, 'refresh', fail)
    assert recommendation_refresh.main(['refresh']) == 1


def test_projected_context_preserves_nested_decisions(tmp_path, monkeypatch):
    from runtime_db import RuntimeDatabase
    import today_combo
    db = RuntimeDatabase(tmp_path / 'context.sqlite')
    game = {'date': '09.25', 'league': 'L', 'home': 'H', 'away': 'A',
            'options': [{'selection_id': 'x'}], 'decision_snapshot': {'selection_id': 'x'},
            '경기근거': {'key': [1]}, 'unused_large_field': 'do not retain'}
    db.store_artifact('picks_v2', {'live': [game], 'past': [{**game, 'home': 'other'}]})
    monkeypatch.setattr(today_combo, 'database_enabled', lambda: True)
    monkeypatch.setattr(today_combo, 'RuntimeDatabase', lambda: db)
    index = today_combo._game_context_index({('09.25', 'L', 'H', 'A')})
    assert len(index) == 1
    projected = next(iter(index.values()))
    for field in ('options', 'decision_snapshot', '경기근거'):
        assert projected[field] == game[field]
    assert 'unused_large_field' not in projected
