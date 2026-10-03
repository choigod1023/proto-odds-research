"""A fresh odds artifact is not proof that the decision artifact was published."""
import pytest
from src import odds_live, live_market_refresh


@pytest.mark.parametrize("result", [0, 1])
def test_market_cli_preserves_refresh_exit_code(monkeypatch, result):
    monkeypatch.setattr(live_market_refresh, "refresh_once", lambda: result)
    assert live_market_refresh.main(["refresh"]) == result


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("result", [0, 1])
def test_odds_cli_does_not_hide_failed_decision_refresh(monkeypatch, empty, result):
    artifact = {"rounds": [117], "n": 1, "generated_at": "2026-10-03T00:00:00Z"}
    monkeypatch.setattr(odds_live, "load_artifact", lambda *a: artifact)
    monkeypatch.setattr(odds_live, "collect", lambda *a: {})
    monkeypatch.setattr(odds_live, "merge_market_history", lambda *a: {} if empty else artifact)
    monkeypatch.setattr(odds_live, "_carry_forward_prices", lambda *a: artifact)
    saved = []
    monkeypatch.setattr(odds_live, "persist_artifact", lambda name, *a, **kw: saved.append(name))
    monkeypatch.setattr(odds_live, "refresh_once", lambda *a: result)
    assert odds_live.main(["odds"]) == result
    assert saved == ["live_odds"]
