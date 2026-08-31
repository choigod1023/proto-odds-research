from src import odds_live


def test_main_refreshes_picks_immediately_after_persist(monkeypatch):
    collected = {"generated_at": "2026-08-31T00:00:00+00:00", "n": 1,
                 "rounds": [104], "markets": {}, "odds": {}}
    calls = []
    monkeypatch.setattr(odds_live, "collect", lambda: collected)
    monkeypatch.setattr(odds_live, "persist_artifact",
                        lambda *args, **kwargs: calls.append(("persist", args[1])))
    monkeypatch.setattr(odds_live, "refresh_once",
                        lambda data: calls.append(("refresh", data)) or 0)

    assert odds_live.main(["odds_live.py"]) == 0
    assert calls == [("persist", collected), ("refresh", collected)]
