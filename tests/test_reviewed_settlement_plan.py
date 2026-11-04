import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reviewed_settlement_plan import read_plan, apply_reviewed_plan
from tests.test_official_result_backfill import inputs, plan
from tests.test_live_market_refresh import admissible_votes_for_offer_ledger_regressions


def reviewed(runtime, row, sid):
    doc = plan(runtime, row, sid)
    entry = doc["entries"][0]
    entry["expected_prediction_hash"] = runtime.records()[0]["record_hash"]
    entry["source"]["official_source"] = dict(
        url="https://www.betman.co.kr/main/mainPage/gamebuy/closedGameSlipIFR.do?gmId=G101&gmTs=260102&isIFR=Y",
        evidence_sha256="a" * 64, received_at="2026-12-31T00:00:00Z")
    return dict(version="reviewed-official-settlements-v1", entries=[entry])


def test_hash_pinned_dry_run_and_idempotent_append_preserve_prediction(tmp_path):
    runtime, row, sid = inputs(tmp_path)
    runtime.ledger._clock = lambda: datetime(2027, 1, 2, tzinfo=timezone.utc)
    doc = reviewed(runtime, row, sid)
    raw = json.dumps(doc).encode()
    loaded = read_plan(raw, hashlib.sha256(raw).hexdigest())
    before = runtime.ledger.records()[0]
    assert apply_reviewed_plan(runtime.ledger, loaded)["dry_run"]
    assert len(runtime.ledger.records()) == 1
    assert apply_reviewed_plan(runtime.ledger, loaded, apply=True)["appended"] == 1
    assert apply_reviewed_plan(runtime.ledger, loaded, apply=True)["appended"] == 0
    assert runtime.ledger.records()[0] == before
    with pytest.raises(ValueError, match="hash differs"):
        read_plan(raw + b" ", hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("change", ["record_hash", "side", "source", "duplicate"])
def test_invalid_reviewed_plan_never_partially_writes(tmp_path, change):
    runtime, row, sid = inputs(tmp_path)
    doc = reviewed(runtime, row, sid)
    if change == "record_hash": doc["entries"][0]["expected_prediction_hash"] = "changed"
    if change == "side": doc["entries"][0]["outcome"]["selection_id"] = "wrong"
    if change == "source": doc["entries"][0]["source"]["official_source"]["url"] = "https://example.org"
    if change == "duplicate": doc["entries"].append(copy.deepcopy(doc["entries"][0]))
    with pytest.raises(ValueError):
        apply_reviewed_plan(runtime.ledger, doc, apply=True)
    assert len(runtime.ledger.records()) == 1


def test_score_derived_plan_preserves_terms_and_checks_every_source(tmp_path):
    runtime, row, sid = inputs(tmp_path)
    runtime.ledger._clock = lambda: datetime(2027, 1, 2, tzinfo=timezone.utc)
    doc = reviewed(runtime, row, sid)
    source = doc["entries"][0]["source"]
    evidence = source.pop("official_source")
    source.update(mode="official_score_with_frozen_terms", original_market="승패",
                  original_label="", official_scores=[dict(official_source=evidence)])
    assert apply_reviewed_plan(runtime.ledger, doc)["validated"] == 1
    source["original_market"] = "언더오버"
    with pytest.raises(ValueError, match="frozen result terms changed"):
        apply_reviewed_plan(runtime.ledger, doc, apply=True)
    source["original_market"] = "승패"
    source["official_scores"].append(dict(official_source={**evidence, "url": "https://example.org"}))
    with pytest.raises(ValueError, match="official evidence"):
        apply_reviewed_plan(runtime.ledger, doc, apply=True)
    assert len(runtime.ledger.records()) == 1


def test_cli_rejects_a_different_database_lock_path_before_opening_ledger(tmp_path, monkeypatch):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("reviewed_plan_cli", Path(__file__).resolve().parents[1] / "scripts/apply_reviewed_settlement_plan.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    raw = json.dumps(dict(version="reviewed-official-settlements-v1", entries=[])).encode()
    path = tmp_path / "plan.json"
    path.write_bytes(raw)
    database = tmp_path / "must-not-open.sqlite3"
    monkeypatch.setenv("PROODD_DB_PATH", str(database))
    monkeypatch.setattr(sys, "argv", ["apply", "--plan", str(path), "--approved-sha256", hashlib.sha256(raw).hexdigest(), "--ledger", str(tmp_path / "other-lock.jsonl")])
    with pytest.raises(ValueError, match="canonical shared ledger lock"):
        cli.main()
    assert not database.exists()
