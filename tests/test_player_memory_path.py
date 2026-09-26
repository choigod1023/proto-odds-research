import importlib.util
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runtime_db import RuntimeDatabase


def test_projection_excludes_history_and_retains_ambiguity(tmp_path):
    db = RuntimeDatabase(tmp_path / "test.db")
    fixture = dict(league="MLB", home_team="H", away_team="A",
                   game_datetime="2026-09-26T18:00:00+09:00", game_id="one")
    db.put_document("player_info", {"games": [
        dict(fixture, home_team="other", padding="x"*100000), fixture]})
    assert db.player_fixture_document("MLB", "H", "A", "2026-09-26T09:00:00Z") == {"games": [fixture]}
    db.put_document("player_info", {"games": [fixture]*3})
    assert len(db.player_fixture_document("MLB", "H", "A", "2026-09-26T09:00:00Z")["games"]) == 2
    assert db.player_fixture_document("MLB", "H", "A", "2026-09-27T09:00:00Z") == {"games": []}


def test_player_wakeup_rechecks_memory_gate(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "deploy" / "supervisor.py"
    spec = importlib.util.spec_from_file_location("player_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    samples = iter([100, 500, 600])
    monkeypatch.setattr(module, "_available_memory_mb", lambda: next(samples))
    monkeypatch.setattr(module, "BACKGROUND_MIN_AVAILABLE_MB", 512)
    waits = []
    class Wake:
        def wait(self, timeout):
            assert not module._memory_heavy_lock.locked()
            waits.append(timeout)
        def clear(self):
            pass
    monkeypatch.setattr(module, "_player_memory_opportunity", Wake())
    with module._background_slot("선수·팀 정보"):
        assert module._memory_heavy_lock.locked()
    assert waits == [30, 30]
    assert not module._memory_heavy_lock.locked()
