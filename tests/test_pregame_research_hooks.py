from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def test_kbo_captures_after_persist(monkeypatch):
    import kbo_pregame as module
    import pregame_research_capture as research
    calls = []
    doc = {"games": [], "errors": [], "generated_at": "2026-10-24T00:00:00Z"}
    monkeypatch.setattr(module, "collect", lambda: doc)
    monkeypatch.setattr(module, "persist_document", lambda *a: calls.append("persist"))
    monkeypatch.setattr(research, "safe_capture", lambda: calls.append("capture"))
    assert module.main() == 0
    assert calls == ["persist", "capture"]


def test_player_captures_after_persist(tmp_path, monkeypatch):
    import player_info as module
    import runtime_db
    import pregame_research_capture as research
    calls = []
    monkeypatch.setattr(runtime_db, "database_enabled", lambda: True)
    monkeypatch.setattr(runtime_db, "RuntimeDatabase", lambda: SimpleNamespace(
        put_document=lambda *a: calls.append("persist")))
    monkeypatch.setattr(module, "load_document", lambda *a: {})
    monkeypatch.setattr(module, "announcement_games", lambda: [])
    monkeypatch.setattr(module, "_session", lambda: None)
    monkeypatch.setattr(module, "collect_npb_games", lambda *a, **k: [])
    monkeypatch.setattr(module, "mlb_games", lambda *a: ([], {}, None))
    monkeypatch.setattr(module, "collect_soccer_info", lambda *a: ([], {}))
    monkeypatch.setattr(module, "collect_court_info", lambda *a: ([], {}))
    monkeypatch.setattr(research, "safe_capture", lambda: calls.append("capture"))
    assert module.collect()["games"] == []
    assert calls == ["persist", "capture"]


@pytest.mark.parametrize("changed", [0, 1])
def test_market_capture_runs_even_without_price_change(monkeypatch, changed):
    import live_market_refresh as module
    import pregame_research_capture as research
    calls = []
    doc = {"live": [], "past": []}
    fake = SimpleNamespace(ledger=SimpleNamespace(verified_session=nullcontext),
                           ui_records=lambda: {})
    monkeypatch.setattr(module, "load_artifact", lambda *a: doc)
    monkeypatch.setattr(module, "refresh_document", lambda *a: (doc, changed))
    monkeypatch.setattr(module, "PredictionRuntime", lambda *a: fake)
    monkeypatch.setattr(module, "settle_live_market_results", lambda *a: 0)
    monkeypatch.setattr(module, "record_live_market_revisions", lambda *a: {})
    monkeypatch.setattr(module, "persist_artifact", lambda *a: calls.append("persist"))
    monkeypatch.setattr(research, "safe_capture", lambda: calls.append("capture"))
    assert module.refresh_once({"generated_at": "2026-10-24T00:00:00Z"}) == 0
    assert calls == (["persist", "capture"] if changed else ["capture"])


def test_real_research_subprocess_preserves_source_database(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import gc
    import hashlib
    from tests.test_pregame_research_capture import runtime, document, fixture, record
    from pregame_research_capture import safe_capture
    from pregame_research_store import PregameResearchStore
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).replace(second=0, microsecond=0)
    local = start.astimezone(timezone(timedelta(hours=9)))
    source = document()
    source['games'][0].update(game_datetime=start.isoformat(),
                             updated_at=(now-timedelta(minutes=1)).isoformat())
    game = fixture()
    game.update(year=local.year, date=local.strftime('%m.%d %H:%M'))
    prediction = record()
    prediction.update(kickoff=start.isoformat(), as_of=(now-timedelta(minutes=5)).isoformat(),
                      market_observed_at=(now-timedelta(minutes=5)).isoformat(),
                      captured_at=(now-timedelta(minutes=4)).isoformat())
    path = runtime(tmp_path, source=source, game=game, prediction=prediction)
    gc.collect()  # Close the fixture helper's SQLite handle before Windows cleanup.
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    target = tmp_path/'research.sqlite3'
    monkeypatch.setenv('PROODD_DB_PATH', str(path))
    monkeypatch.setenv('PROODD_RESEARCH_DB_PATH', str(target))
    result = safe_capture()
    if result.get('reason') in {'memory_available_below_256mib', 'memory_available_unknown'}:
        pytest.skip('Host memory guard deliberately withheld research subprocess')
    assert result['status'] == 'ok' and result['captures_processed'] == 1
    store = PregameResearchStore(target, official_sources={'proto_official'}, read_only=True)
    rows = list(store.export_paired())
    assert len(rows) == 1 and rows[0]['incomplete_flags'] == ['missing_official_result']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
