from copy import deepcopy
from datetime import UTC, datetime

import pytest

from src.live_market_refresh import (
    refresh_document, record_live_market_revisions, settle_live_market_results,
)
from src.prediction_runtime import PredictionRuntime


def feed():
    return {"generated_at": "2026-08-30T01:00:00+00:00", "markets": {"102": {
        "7100": {"game_no": "7100", "date": "08.30(일) 18:00", "sport": "bs",
                 "league": "KBO", "home": "KIA", "away": "SSG", "market": "승패",
                 "label": "", "n_way": 2, "odds": [1.55, 2.05], "result": "경기전"}}}}


def setup_runtime(tmp_path):
    odds = feed()
    doc, _ = refresh_document({"live": [], "past": []}, odds)
    clock = [datetime(2026, 8, 30, 1, 5, tzinfo=UTC)]
    runtime = PredictionRuntime(tmp_path / "ledger.jsonl", clock=lambda: clock[0])
    record_live_market_revisions(doc, odds["generated_at"], runtime)
    clock[0] = datetime(2026, 8, 30, 14, tzinfo=UTC)
    odds["generated_at"] = "2026-08-30T13:00:00+00:00"
    odds["markets"]["102"]["7100"]["result"] = "홈승"
    return runtime, doc, odds


def test_frozen_prices_still_settle_and_retry_is_idempotent(tmp_path):
    runtime, doc, odds = setup_runtime(tmp_path)
    before = deepcopy(runtime.records()[0])
    refreshed, changed = refresh_document(doc, odds)
    assert changed == 0  # The production path previously returned here.
    assert settle_live_market_results(odds, runtime) == 1
    assert settle_live_market_results(odds, runtime) == 0
    assert runtime.records()[0] == before
    assert runtime.records()[-1]["snapshot_id"] == before["snapshot_id"]
    assert runtime.records()[-1]["outcome"]["result"] == "hit"


@pytest.mark.parametrize("field,value", [
    ("game_no", "9999"), ("date", "08.29(토) 18:00"), ("home", "다른팀"),
    ("league", "NPB"), ("label", "다른기준"), ("result", "미확인"),
])
def test_no_fuzzy_or_cross_offer_settlement(tmp_path, field, value):
    runtime, _, odds = setup_runtime(tmp_path)
    odds["markets"]["102"]["7100"][field] = value
    assert settle_live_market_results(odds, runtime) == 0


def test_conflicting_duplicate_results_skipped(tmp_path):
    runtime, _, odds = setup_runtime(tmp_path)
    odds["markets"]["102"]["duplicate"] = {
        **odds["markets"]["102"]["7100"], "result": "홈패"}
    assert settle_live_market_results(odds, runtime) == 0


def test_official_correction_appends_without_rewriting_prediction(tmp_path):
    runtime, _, odds = setup_runtime(tmp_path)
    assert settle_live_market_results(odds, runtime) == 1
    odds["markets"]["102"]["7100"]["result"] = "홈패"
    assert settle_live_market_results(odds, runtime) == 1
    assert [r["outcome"]["result"] for r in runtime.records()[1:]] == ["hit", "miss"]


def test_void_and_before_kickoff(tmp_path):
    runtime, _, odds = setup_runtime(tmp_path)
    odds["generated_at"] = "2026-08-30T02:00:00+00:00"
    assert settle_live_market_results(odds, runtime) == 0
    odds["generated_at"] = "2026-08-30T13:00:00+00:00"
    odds["markets"]["102"]["7100"]["result"] = "취소"
    assert settle_live_market_results(odds, runtime) == 1
    assert runtime.records()[-1]["outcome"]["result"] == "void"


def test_refresh_once_calls_settlement_on_unchanged_document(tmp_path, monkeypatch):
    import src.live_market_refresh as module
    runtime, doc, odds = setup_runtime(tmp_path)
    monkeypatch.setattr(module, "PredictionRuntime", lambda path: runtime)
    monkeypatch.setattr(module, "load_artifact", lambda *args: doc)
    published = []
    monkeypatch.setattr(module, "persist_artifact", lambda key, value, path: published.append(value))
    assert module.refresh_once(odds) == 0
    assert published[0]["live_market_refresh"]["ledger_sync"]["settlements"] == 1
    assert published[0]["live"][0]["prediction_record"]["result"] == "hit"


def test_retry_repairs_ui_after_ledger_already_committed(tmp_path, monkeypatch):
    import src.live_market_refresh as module
    runtime, doc, odds = setup_runtime(tmp_path)
    assert settle_live_market_results(odds, runtime) == 1
    monkeypatch.setattr(module, "PredictionRuntime", lambda path: runtime)
    monkeypatch.setattr(module, "load_artifact", lambda *args: doc)
    published = []
    monkeypatch.setattr(module, "persist_artifact", lambda key, value, path: published.append(value))
    assert module.refresh_once(odds) == 0
    assert published[0]["live"][0]["prediction_record"]["result"] == "hit"
    assert len(runtime.records()) == 2
